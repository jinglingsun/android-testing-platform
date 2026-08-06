from platform_code.algorithms import AlgorithmContext, Explorer
from platform_code.actions import Action


class LeastVisitedExample(Explorer):
    name = "least_visited_example"

    def choose(self, state_hash: str, actions: list[Action], ctx: AlgorithmContext) -> Action:
        return min(actions, key=lambda action: sum(1 for known, _ in ctx.graph.edges.get(state_hash, []) if known == action))
