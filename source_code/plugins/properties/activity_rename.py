from string import ascii_letters

from hypothesis.strategies import text

from platform_code.properties import PreconditionFailed, PropertyContext


def test_activity_rename(ctx: PropertyContext) -> None:
    selector = {"resourceId": "de.rampro.activitydiary:id/activity_name"}
    ctx.require_exists(**selector)

    old_name = ctx.d(**selector).info.get("text") or ""
    if not old_name:
        raise PreconditionFailed("activity_name text is empty")
    ctx.require_count(1, resourceId="de.rampro.activitydiary:id/activity_name", text=old_name)
    existing_names = set(ctx.texts(resourceId="de.rampro.activitydiary:id/activity_name"))
    new_name = _unique_random_text(existing_names)

    ctx.long_click(**selector)

    edit_selector = {"resourceId": "de.rampro.activitydiary:id/edit_activity_name"}
    ctx.require_exists(**edit_selector)
    ctx.set_text(new_name, **edit_selector)

    done_selector = {"resourceId": "de.rampro.activitydiary:id/action_edit_done"}
    ctx.require_exists(**done_selector)
    ctx.tap(**done_selector)

    ctx.final_assert_exists(text=new_name)
    ctx.final_assert_not_exists(resourceId="de.rampro.activitydiary:id/activity_name", text=old_name)
    activity_names = ctx.state.setdefault("activity_names", set())
    activity_names.discard(old_name)
    activity_names.add(new_name)


def _unique_random_text(existing_names: set[str]) -> str:
    for _ in range(30):
        value = text(alphabet=ascii_letters, min_size=1, max_size=12).example()
        if value not in existing_names:
            return value
    raise AssertionError("cannot generate a unique activity name")
