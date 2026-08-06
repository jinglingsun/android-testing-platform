from collections import Counter

from platform_code.properties import PreconditionFailed, PropertyContext


ACTIVITY_NAME_ID = "de.rampro.activitydiary:id/activity_name"
DELETE_BUTTON_ID = "de.rampro.activitydiary:id/action_edit_delete"


def test_activity_delete(ctx: PropertyContext) -> None:
    selector = {"resourceId": "de.rampro.activitydiary:id/activity_name"}
    ctx.require_exists(**selector)

    old_name = ctx.d(**selector).info.get("text") or ""
    if not old_name:
        raise PreconditionFailed("activity_name text is empty")
    ctx.require_count(1, resourceId="de.rampro.activitydiary:id/activity_name", text=old_name)

    ctx.long_click(**selector)

    ctx.require_exists(resourceId=DELETE_BUTTON_ID)
    ctx.tap(resourceId=DELETE_BUTTON_ID)

    ctx.final_assert_not_exists(text=old_name)
    ctx.state.setdefault("activity_names", set()).discard(old_name)
