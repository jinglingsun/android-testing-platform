from string import ascii_letters

from hypothesis.strategies import text

from platform_code.properties import PropertyContext


def test_input_box(ctx: PropertyContext) -> None:
    ctx.require_exists(description="input_box")

    random_str = text(alphabet=ascii_letters, min_size=1, max_size=12).example()
    ctx.set_text(random_str, description="input_box")

    ctx.require_exists(description="send_button")
    ctx.final_assert_exists(text=random_str)
