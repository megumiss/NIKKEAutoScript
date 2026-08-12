"""In-memory game screen preview frames."""

from starlette.responses import JSONResponse, Response

from module.webui.process_manager import ProcessManager


async def screenshot(request):
    """Return the latest preview frame published by the instance worker."""
    name = request.path_params['name']
    # Avoid get_manager() here: it would create a manager for unknown names.
    manager = ProcessManager._processes.get(name)
    preview = manager.latest_preview if manager else None
    if preview is None:
        return JSONResponse({'error': 'no preview'}, status_code=404)
    captured_at, data = preview
    return Response(
        content=data,
        media_type='image/jpeg',
        headers={'X-Captured-At': str(captured_at)},
    )
