from http import HTTPStatus

import eyecite
from asgiref.sync import async_to_sync
from django.db.models import QuerySet
from django.http import HttpResponse
from django.template.defaultfilters import slugify
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.mixins import ListModelMixin
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from cl.citations.api_serializers import CitationRequestSerializer
from cl.citations.exceptions import MultipleChoices
from cl.citations.utils import SLUGIFIED_EDITIONS, get_canonicals_from_reporter
from cl.search.api_serializers import OpinionClusterSerializer
from cl.search.models import OpinionCluster
from cl.search.selectors import get_clusters_from_citation_str


class CitationLookupViewSet(ListModelMixin, GenericViewSet):
    queryset = OpinionCluster.objects.all()
    serializer = OpinionClusterSerializer

    def list(self, request: Request, *args, **kwargs):
        query = request.query_params

        # Uses the serializer to perform object level validations
        citation_serializer = CitationRequestSerializer(data=query)
        citation_serializer.is_valid(raise_exception=True)

        # Get query parameters from the validated data
        data = citation_serializer.validated_data
        self.full_text_citation = data.get("text_citation", None)
        self.reporter = data.get("reporter", None)
        self.volume = data.get("volume", None)
        self.page = data.get("citation_page", None)

        if self.full_text_citation:
            citations = eyecite.get_citations(self.full_text_citation)
            if not citations:
                raise NotFound(f"No citations found in 'text_citation'.")

            if not citations[0].groups:
                raise ValidationError({"text_citation": ["Invalid citation."]})

            c = citations[0]
            self.reporter = c.groups["reporter"]
            self.volume = c.groups["volume"]
            self.page = c.groups["page"]

        self.reporter_slug = slugify(self.reporter)

        # Look up the reporter to get its proper version (so-2d -> So. 2d)
        proper_reporter = SLUGIFIED_EDITIONS.get(self.reporter_slug, None)
        if not proper_reporter:
            proper_reporter = self._attempt_reporter_variation()

        return self._citation_handler(request, proper_reporter)

    def _attempt_reporter_variation(self) -> str:
        """Try to disambiguate an unknown reporter using the variations dict.

        Raises:
            NotFound: If the unknown reporter string doesn't match a canonical.
            MultipleChoices: If the reporter variation maps to more than one
                            canonical reporter.

        Returns:
            str: canonical name for the reporter.
        """
        potential_canonicals = get_canonicals_from_reporter(self.reporter_slug)

        if len(potential_canonicals) == 0:
            # Couldn't find it as a variation. Give up.
            raise NotFound(
                f"Unable to find reporter with abbreviation of '{self.reporter}'"
            )

        elif len(potential_canonicals) > 1:
            # The reporter variation is ambiguous b/c it can refer to more than
            # one reporter. Abort with a 300 status.
            raise MultipleChoices(
                f"Found more than one possible reporter for '{self.reporter}'"
            )

        else:
            # Unambiguous reporter variation. Use the canonical reporter
            return SLUGIFIED_EDITIONS.get(potential_canonicals[0], "")

    def _citation_handler(
        self, request: Request, reporter: str
    ) -> HttpResponse:
        """
        This method retrieves opinion clusters that match a citation string.

        Args:
            request (Request): The HTTP object containing information
                about the request.
            reporter (str): The name of the reporter.

        Raises:
            NotFound: If the citation string doesn't match any opinion clusters

        Returns:
            HttpResponse: An HTTP response object containing a list of matching
                opinion clusters.
        """
        citation_str = " ".join([str(self.volume), reporter, self.page])
        clusters, cluster_count = async_to_sync(
            get_clusters_from_citation_str
        )(reporter, str(self.volume), self.page)

        if cluster_count == 0:
            raise NotFound(f"Citation not found: '{ citation_str }'")

        return self._show_response(request, clusters)

    def _show_response(
        self, request: Request, clusters: QuerySet[OpinionCluster]
    ) -> HttpResponse:
        """
        Generates a paginated HTTP response containing opinion clusters.

        This method takes a queryset of opinion clusters and an HTTP request
        object. It then paginates the queryset based on the request parameters
        and returns an appropriate HTTP response containing the paginated data.

        Args:
            request (Request): The HTTP request object.
            clusters (QuerySet[OpinionCluster]): The queryset containing opinion
            clusters.

        Returns:
            HttpResponse: An HTTP response containing the paginated opinion
                        clusters and pagination information.
        """
        clusters = clusters.prefetch_related(
            "sub_opinions", "panel", "non_participating_judges", "citations"
        ).order_by("-id")
        serializer = self.serializer(
            clusters, many=True, context={"request": request}
        )
        return Response(
            serializer.data,
            status=(
                HTTPStatus.MULTIPLE_CHOICES
                if clusters.count() > 1
                else HTTPStatus.OK
            ),
        )
