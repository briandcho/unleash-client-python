from UnleashClient.utils import normalized_hash
from UnleashClient.strategies import Strategy


class GradualRolloutSessionId(Strategy):
    def __call__(self, context: dict = None) -> bool:
        """
        Returns true if userId is a member of id list.

        :return:
        """
        percentage = self.parameters["percentage"]
        activation_group = self.parameters["groupId"]

        return percentage < normalized_hash(context["sessionId"], activation_group)
