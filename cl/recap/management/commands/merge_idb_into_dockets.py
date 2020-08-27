import os

from celery.canvas import chain
from django.conf import settings
from juriscraper.lib.string_utils import CaseNameTweaker, harmonize
from juriscraper.pacer import PacerSession

from cl.corpus_importer.tasks import (
    make_fjc_idb_lookup_params,
    get_pacer_case_id_and_title,
)
from cl.lib.celery_utils import CeleryThrottle
from cl.lib.command_utils import VerboseCommand, CommandUtils, logger
from cl.lib.string_diff import find_best_match
from cl.recap.constants import CV_2017
from cl.recap.models import FjcIntegratedDatabase
from cl.recap.tasks import (
    merge_docket_with_idb,
    create_new_docket_from_idb,
    update_docket_from_hidden_api,
)
from cl.search.models import Docket

cnt = CaseNameTweaker()

PACER_USERNAME = os.environ.get("PACER_USERNAME", settings.PACER_USERNAME)
PACER_PASSWORD = os.environ.get("PACER_PASSWORD", settings.PACER_PASSWORD)


def do_heuristic_match(idb_row, ds):
    """Use cosine similarity of case names to try to find a match out of
    several possibilities.

    :param idb_row: The FJC IDB row to match against
    :param ds: A list of Dockets that might match
    :returns: The best-matching Docket in ds if possible, else None
    """
    case_names = []
    for d in ds:
        case_name = harmonize(d.case_name)
        parts = case_name.lower().split(" v. ")
        if len(parts) == 1:
            case_names.append(case_name)
        elif len(parts) == 2:
            plaintiff, defendant = parts[0], parts[1]
            case_names.append("%s v. %s" % (plaintiff[0:30], defendant[0:30]))
        elif len(parts) > 2:
            case_names.append(case_name)
    idb_case_name = harmonize(
        "%s v. %s" % (idb_row.plaintiff, idb_row.defendant)
    )
    results = find_best_match(case_names, idb_case_name, case_sensitive=False)
    if results["ratio"] > 0.65:
        logger.info(
            "Found good match by case name for %s: %s",
            idb_case_name,
            results["match_str"],
        )
        d = ds[results["match_index"]]
    else:
        logger.info(
            "No good match after office and case name filtering. Creating "
            "new item: %s",
            idb_row,
        )
        d = None
    return d


class Command(VerboseCommand, CommandUtils):
    help = (
        "Iterate over the IDB data and merge it into our existing "
        "datasets. Where we lack a Docket object for an item in the IDB, "
        "create one."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--queue",
            default="batch1",
            help="The celery queue where the tasks should be processed.",
        )
        parser.add_argument(
            "--offset",
            type=int,
            default=0,
            help="The number of items to skip before beginning. Default is to "
            "skip none.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="After doing this number, stop. This number is not additive "
            "with the offset parameter. Default is to do all of them.",
        )
        parser.add_argument(
            "--task",
            type=str,
            required=True,
            help="What task are we doing at this point?",
        )

    def handle(self, *args, **options):
        logger.info("Using PACER username: %s" % PACER_USERNAME)
        if options["task"] == "merge_and_create":
            self.join_fjc_with_dockets(options)
        elif options["task"] == "update_case_ids":
            self.update_any_missing_pacer_case_ids(options)

    @staticmethod
    def join_fjc_with_dockets(options):
        idb_rows = FjcIntegratedDatabase.objects.filter(
            dataset_source=CV_2017,
        ).order_by("pk")
        if options["court_id"]:
            idb_rows = idb_rows.filter(district_id=options["court_id"])

        logger.info("%s items will be merged or created.", idb_rows.count())
        q = options["queue"]
        throttle = CeleryThrottle(queue_name=q)
        for i, idb_row in enumerate(idb_rows.iterator()):
            # Iterate over all items in the IDB and find them in the Docket
            # table. If they're not there, create a new item.
            if i < options["offset"]:
                continue
            if i >= options["limit"] > 0:
                break
            throttle.maybe_wait()
            ds = (
                Docket.objects.filter(
                    docket_number_core=idb_row.docket_number,
                    court=idb_row.district,
                )
                .exclude(docket_number__icontains="cr")
                .exclude(case_name__icontains="sealed")
                .exclude(case_name__icontains="suppressed")
                .exclude(case_name__icontains="search warrant")
            )
            count = ds.count()
            if count == 0:
                msg = "%s: Creating new docket for IDB row: %s"
                logger.info(msg, i, idb_row)
                create_new_docket_from_idb.apply_async(
                    args=(idb_row.pk,), queue=q
                )
                continue
            elif count == 1:
                d = ds[0]
                msg = "%s: Merging Docket %s with IDB row: %s"
                logger.info(msg, i, d, idb_row)
                merge_docket_with_idb.apply_async(
                    args=(d.pk, idb_row.pk), queue=q
                )
                continue

            msg = "%s: Unable to merge. Got %s dockets for row: %s"
            logger.info(msg, i, count, idb_row)

            d = do_heuristic_match(idb_row, ds)
            if d is not None:
                merge_docket_with_idb(d.pk, idb_row.pk)
            else:
                create_new_docket_from_idb(idb_row.pk)

    @staticmethod
    def update_any_missing_pacer_case_ids(options):
        """The network requests were making things far too slow and had to be
        disabled during the first pass. With this method, we update any items
        that are missing their pacer case ID value.
        """
        ds = Docket.objects.filter(idb_data__isnull=False, pacer_case_id=None)
        q = options["queue"]
        throttle = CeleryThrottle(queue_name=q)
        session = PacerSession(
            username=PACER_USERNAME, password=PACER_PASSWORD
        )
        session.login()
        for i, d in enumerate(ds.iterator()):
            if i < options["offset"]:
                continue
            if i >= options["limit"] > 0:
                break

            if i % 5000 == 0:
                # Re-authenticate just in case the auto-login mechanism isn't
                # working.
                session = PacerSession(
                    username=PACER_USERNAME, password=PACER_PASSWORD
                )
                session.login()

            throttle.maybe_wait()
            logger.info("Getting pacer_case_id for item %s", d)
            params = make_fjc_idb_lookup_params(d.idb_data)
            chain(
                get_pacer_case_id_and_title.s(
                    pass_through=d.pk,
                    docket_number=d.idb_data.docket_number,
                    court_id=d.idb_data.district_id,
                    cookies=session.cookies,
                    **params
                ).set(queue=q),
                update_docket_from_hidden_api.s().set(queue=q),
            ).apply_async()
