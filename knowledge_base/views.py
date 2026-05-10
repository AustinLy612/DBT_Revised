from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .services import (
    hybrid_search,
    keyword_search,
    log_retrieval,
    semantic_search,
)


@staff_member_required
@require_http_methods(["GET"])
def search_view(request):
    """Retrieval endpoint: ?q=...&mode=keyword|semantic|hybrid&use_case=teaching&session_id=..."""
    query = request.GET.get("q", "").strip()
    mode = request.GET.get("mode", "keyword")
    use_case = request.GET.get("use_case", "teaching")
    session_id = request.GET.get("session_id")

    if not query:
        return JsonResponse({"error": "Missing query parameter 'q'"}, status=400)

    search_fn = {
        "keyword": keyword_search,
        "semantic": semantic_search,
        "hybrid": hybrid_search,
    }.get(mode, keyword_search)

    results = search_fn(query)

    retrieved_ids = [r["chunk_id"] for r in results]

    session = None
    if session_id:
        from teaching.models import TeachingSession
        try:
            session = TeachingSession.objects.get(session_id=session_id)
        except TeachingSession.DoesNotExist:
            pass

    log_retrieval(
        user=request.user,
        session=session,
        query=query,
        retrieved_chunk_ids=retrieved_ids,
        use_case=use_case,
    )

    return JsonResponse({
        "query": query,
        "mode": mode,
        "count": len(results),
        "results": results,
    })
