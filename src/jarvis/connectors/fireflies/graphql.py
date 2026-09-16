"""The Fireflies surface, expressed as a fixed set of queries.

GraphQL has no route table to restrict: every call is one POST to one path, so the request
body is the whole surface. The boundary therefore lives here instead — the query documents
below are constants in trusted code, and a caller supplies only typed variables. Nothing a
model writes ever becomes query text, which is the same rule the calendar follows for its
search word, made stronger by the document itself being fixed.

Only reads are declared. Fireflies can delete a transcript; that capability is deliberately
absent rather than guarded, because a connector cannot misuse what it cannot express.
"""

from typing import Literal

from jarvis.connectors.http import Route, ServiceTransport, TransportError

ORIGIN = "https://api.fireflies.ai"
ROUTES = (Route("POST", r"/graphql"),)

MEETINGS = """
query Meetings($limit: Int, $fromDate: DateTime, $toDate: DateTime,
               $keyword: String, $participants: [String]) {
  transcripts(limit: $limit, fromDate: $fromDate, toDate: $toDate,
              keyword: $keyword, participants: $participants) {
    id
    title
    date
    duration
    organizer_email
    participants
  }
}
"""

MEETING = """
query Meeting($id: String!) {
  transcript(id: $id) {
    id
    title
    date
    duration
    organizer_email
    participants
    summary {
      overview
      action_items
      keywords
      bullet_gist
    }
  }
}
"""

TRANSCRIPT = """
query Transcript($id: String!) {
  transcript(id: $id) {
    id
    title
    date
    duration
    organizer_email
    participants
    sentences {
      speaker_name
      text
    }
  }
}
"""

Code = Literal[
    "fireflies_credentials",
    "fireflies_denied",
    "fireflies_missing",
    "fireflies_rate_limited",
    "fireflies_network",
    "fireflies_response",
    "fireflies_request",
]


class FirefliesFailure(Exception):
    """Finite public categories; no server text reaches the interface or the audit."""

    def __init__(self, code: Code) -> None:
        self.code = code
        super().__init__(code)


FAILURES: dict[str, Code] = {
    "route_denied": "fireflies_request",
    "credentials": "fireflies_credentials",
    "forbidden": "fireflies_denied",
    "not_found": "fireflies_missing",
    # A free plan allows fifty calls a day, so this is an ordinary outcome, not an anomaly.
    "rate_limited": "fireflies_rate_limited",
    "response_invalid": "fireflies_response",
    "response_limit": "fireflies_response",
    "response_unparsable": "fireflies_response",
}


def transport() -> ServiceTransport:
    return ServiceTransport(
        ORIGIN,
        ROUTES,
        headers={"Accept": "application/json"},
        timeout_seconds=20,
        # A full hour of speech is large; the tool result stays bounded separately.
        max_response_bytes=1048576,
        success_statuses=(200,),
    )


def translate(error: TransportError) -> FirefliesFailure:
    return FirefliesFailure(FAILURES.get(error.code, "fireflies_network"))
