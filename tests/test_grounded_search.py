from types import SimpleNamespace

import httpx

from ufc_agent.search.grounded import REDIRECT_PREFIX, parse_response


def fake_response(text, chunks):
    metadata = SimpleNamespace(grounding_chunks=[SimpleNamespace(web=SimpleNamespace(title=t, uri=u)) for t, u in chunks])
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(grounding_metadata=metadata)])


def test_parse_response_resolves_redirects_and_dedupes():
    redirect = REDIRECT_PREFIX + "abc"

    def handler(request):
        return httpx.Response(302, headers={"location": "https://www.ufc.com/event/ufc-322"})

    response = fake_response(
        " Islam Makhachev won. ",
        [("ufc.com", redirect), ("ufc.com", redirect), ("espn.com", "https://www.espn.com/mma/x")],
    )
    result = parse_response("who won", response, httpx.Client(transport=httpx.MockTransport(handler)))
    assert result.answer == "Islam Makhachev won."
    assert [s.url for s in result.sources] == ["https://www.ufc.com/event/ufc-322", "https://www.espn.com/mma/x"]
    assert result.to_dict()["sources"][0]["title"] == "ufc.com"


def test_parse_response_without_grounding():
    response = SimpleNamespace(text="No results.", candidates=[SimpleNamespace(grounding_metadata=None)])
    result = parse_response("q", response)
    assert result.sources == []
