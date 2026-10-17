"""Where a run can be read afterwards: its record page titled, its session saved."""

from . import tooltests


def label_record_page(run, scenario_id, model_id):
    """Title the session's record page after the scenario, and say where to read it.

    The agent already saved it: `notebook_resume` creates a Galaxy page under a slug derived
    from the session id. Untitled that way it is a uuid among hundreds, so this names it.
    """
    if not run.document:
        return None
    config = run.config
    slug = f"olit-{run.document['session']['id']}"
    galaxy = tooltests.Galaxy(config["galaxy_root"], config.get("galaxy_key", ""))
    try:
        pages = galaxy.call(f"api/pages?search=slug:{slug}") or []
        page = next((p for p in pages if p.get("slug") == slug and not p.get("deleted")), None)
        if not page:
            return None
        title = f"eval: {scenario_id} [{model_id}]"
        galaxy.call(f"api/pages/{page['id']}", method="PUT", body={"title": title})
        return f"{config['galaxy_root']}u/{page.get('username')}/p/{slug}"
    except Exception:
        # Reporting must never decide a run; the page is saved either way.
        return None


def save_session(config, scenario_id, model_id, run):
    """Save the run as an Olit visualization, so it reopens in the UI as a conversation.

    The agent saves its record page itself; saving the conversation is the page's job, and an
    eval has no page. Without this a run is reviewable only as JSONL.
    """
    if not run.document:
        return None
    root = config["galaxy_root"]
    galaxy = tooltests.Galaxy(root, config.get("galaxy_key", ""))
    title = f"eval: {scenario_id} [{model_id}]"
    document = {**run.document, "session": {**run.document["session"], "title": title}}
    try:
        created = galaxy.call(
            "api/visualizations", method="POST", body={"title": title, "config": document, "type": "olit"}
        )
        return f"{root}visualizations/display?visualization=olit&visualization_id={created['id']}"
    except Exception:
        # Reporting must never decide a run.
        return None
