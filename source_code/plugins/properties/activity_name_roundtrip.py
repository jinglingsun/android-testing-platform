from string import ascii_letters

from hypothesis.strategies import text

from platform_code.properties import PropertyContext


def test_activity_name_roundtrip(ctx: PropertyContext) -> None:
    ctx.require_exists(description="Add Activity")
    ctx.require_not_exists(resourceId="de.rampro.activitydiary:id/edit_activity_name")
    existing_names = set(ctx.texts(resourceId="de.rampro.activitydiary:id/activity_name"))
    name = _unique_random_text(existing_names)

    ctx.tap(description="Add Activity")

    ctx.require_exists(resourceId="de.rampro.activitydiary:id/edit_activity_name")
    ctx.set_text(name, resourceId="de.rampro.activitydiary:id/edit_activity_name")

    ctx.require_exists(resourceId="de.rampro.activitydiary:id/action_edit_done")
    ctx.tap(resourceId="de.rampro.activitydiary:id/action_edit_done")

    ctx.final_assert_exists(text=name)
    ctx.state.setdefault("activity_names", set()).add(name)


def _unique_random_text(existing_names: set[str]) -> str:
    for _ in range(30):
        value = text(alphabet=ascii_letters, min_size=1, max_size=12).example()
        if value not in existing_names:
            return value
    raise AssertionError("cannot generate a unique activity name")
