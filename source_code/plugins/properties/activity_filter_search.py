import random

from platform_code.properties import PreconditionFailed, PropertyContext


FILTER_BUTTON_ID = "de.rampro.activitydiary:id/action_filter"
SEARCH_TEXT_ID = "de.rampro.activitydiary:id/search_src_text"


def test_activity_filter_search(ctx: PropertyContext) -> None:
    ctx.require_exists(resourceId=FILTER_BUTTON_ID)

    activity_names = sorted(ctx.state.get("activity_names", set()))
    if not activity_names:
        raise PreconditionFailed("activity_names set is empty")
    name = random.choice(activity_names)

    ctx.tap(resourceId=FILTER_BUTTON_ID)

    ctx.require_exists(resourceId=SEARCH_TEXT_ID)
    ctx.set_text(name, resourceId=SEARCH_TEXT_ID)

    ctx.final_assert_exists(text=name)
