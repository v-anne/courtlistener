from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db.models import (
    Avg,
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    Q,
    Subquery,
)
from django.db.models.functions import Cast, Extract, Now, Sqrt
from django.template import loader
from django.utils import timezone

from cl.custom_filters.templatetags.pacer import price
from cl.favorites.models import Prayer
from cl.search.models import RECAPDocument


async def prayer_eligible(user: User) -> bool:
    allowed_prayer_count = settings.ALLOWED_PRAYER_COUNT

    now = timezone.now()
    last_24_hours = now - timedelta(hours=24)

    # Count the number of prayers made by this user in the last 24 hours
    prayer_count = await Prayer.objects.filter(
        user=user, date_created__gte=last_24_hours
    ).acount()

    return prayer_count < allowed_prayer_count


async def create_prayer(
    user: User, recap_document: RECAPDocument
) -> Prayer | None:
    if await prayer_eligible(user) and not recap_document.is_available:
        new_prayer, created = await Prayer.objects.aget_or_create(
            user=user, recap_document=recap_document
        )
        return new_prayer if created else None
    return None


async def delete_prayer(user: User, recap_document: RECAPDocument) -> bool:
    deleted, _ = await Prayer.objects.filter(
        user=user, recap_document=recap_document, status=Prayer.WAITING
    ).adelete()

    return deleted > 0


async def get_prayer_counts_in_bulk(
    recap_documents: list[RECAPDocument],
) -> dict[str, int]:
    """Retrieve the count of prayers with a status of "WAITING" for a list of recap documents.

    :param recap_documents: A list of RECAPDocument instances to filter prayers.
    :return: A dictionary where keys are RECAPDocument IDs and values are the
    count of "WAITING" prayers for each document.
    """

    prayer_counts = (
        Prayer.objects.filter(
            recap_document__in=recap_documents, status=Prayer.WAITING
        )
        .values("recap_document")
        .annotate(count=Count("id"))
    )
    return {
        prayer_count["recap_document"]: prayer_count["count"]
        async for prayer_count in prayer_counts
    }


async def get_existing_prayers_in_bulk(
    user: User, recap_documents: list[RECAPDocument]
) -> dict[int, bool]:
    """Check if prayers exist for a user and a list of recap documents.

    :param user: The user for whom to check prayer existence.
    :param recap_documents: A list of RECAPDocument instances to check prayers.
    :return: A dictionary where keys are RECAPDocument IDs and values are True
     if a prayer exists for the user and RD.
    """
    existing_prayers = Prayer.objects.filter(
        user=user, recap_document__in=recap_documents
    ).values_list("recap_document_id", flat=True)
    return {rd_id: True async for rd_id in existing_prayers}


async def get_top_prayers() -> list[RECAPDocument]:
    # Calculate the age of each prayer
    prayer_age = ExpressionWrapper(
        Extract(Now() - F("prayers__date_created"), "epoch"),
        output_field=FloatField(),
    )
    waiting_prayers = Prayer.objects.filter(status=Prayer.WAITING).values(
        "recap_document_id"
    )
    # Annotate each RECAPDocument with the number of prayers and the average prayer age
    documents = (
        RECAPDocument.objects.filter(id__in=Subquery(waiting_prayers))
        .select_related(
            "docket_entry",
            "docket_entry__docket",
            "docket_entry__docket__court",
        )
        .only(
            "pk",
            "document_type",
            "document_number",
            "attachment_number",
            "pacer_doc_id",
            "page_count",
            "description",
            "docket_entry__docket_id",
            "docket_entry__docket__slug",
            "docket_entry__docket__pacer_case_id",
            "docket_entry__docket__court__jurisdiction",
            "docket_entry__docket__court_id",
        )
        .annotate(
            prayer_count=Count(
                "prayers", filter=Q(prayers__status=Prayer.WAITING)
            ),
            avg_prayer_age=Avg(
                prayer_age, filter=Q(prayers__status=Prayer.WAITING)
            ),
        )
        .annotate(
            geometric_mean=Sqrt(
                Cast(
                    F("prayer_count")
                    * Cast(F("avg_prayer_age"), FloatField()),
                    FloatField(),
                )
            )
        )
        .order_by("-geometric_mean")[:50]
    )

    return [doc async for doc in documents.aiterator()]


def send_prayer_emails(instance: RECAPDocument) -> None:
    open_prayers = Prayer.objects.filter(
        recap_document=instance, status=Prayer.WAITING
    ).select_related("user")
    # Retrieve email recipients before updating granted prayers.
    email_recipients = [
        {
            "email": prayer["user__email"],
            "date_created": prayer["date_created"],
        }
        for prayer in open_prayers.values("user__email", "date_created")
    ]
    open_prayers.update(status=Prayer.GRANTED)

    # Send email notifications in bulk.
    if email_recipients:
        subject = f"A document you requested is now on CourtListener"
        txt_template = loader.get_template("prayer_email.txt")
        html_template = loader.get_template("prayer_email.html")

        docket = instance.docket_entry.docket
        docket_entry = instance.docket_entry
        document_url = instance.get_absolute_url()
        num_waiting = len(email_recipients)
        doc_price = price(instance)

        messages = []
        for email_recipient in email_recipients:
            context = {
                "docket": docket,
                "docket_entry": docket_entry,
                "rd": instance,
                "document_url": document_url,
                "num_waiting": num_waiting,
                "price": doc_price,
                "date_created": email_recipient["date_created"],
            }
            txt = txt_template.render(context)
            html = html_template.render(context)
            msg = EmailMultiAlternatives(
                subject=subject,
                body=txt,
                from_email=settings.DEFAULT_ALERTS_EMAIL,
                to=[email_recipient["email"]],
                headers={"X-Entity-Ref-ID": f"prayer.rd.pk:{instance.pk}"},
            )
            msg.attach_alternative(html, "text/html")
            messages.append(msg)
        connection = get_connection()
        connection.send_messages(messages)


async def get_user_prayer_history(user: User) -> tuple[int, float]:
    filtered_list = Prayer.objects.filter(user=user, status=Prayer.GRANTED)

    count = await filtered_list.acount()

    total_cost = 0
    async for prayer in filtered_list:
        total_cost += float(price(prayer.recap_document))

    return count, total_cost


async def get_lifetime_prayer_stats() -> tuple[int, int, float]:

    filtered_list = Prayer.objects.filter(status=Prayer.GRANTED)

    count = await filtered_list.acount()

    total_cost = 0
    distinct_documents = set()

    async for prayer in filtered_list:
        distinct_documents.add(prayer.recap_document)
        total_cost += float(await price(prayer.recap_document))

    num_distinct_purchases = len(distinct_documents)

    return count, num_distinct_purchases, total_cost
