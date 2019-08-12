import abc

import torch

from adept.learner.learner import LearnerMixin


class ActorCriticLearnerMixin(LearnerMixin, metaclass=abc.ABCMeta):
    args = {
        'normalize_advantage': False
    }

    @property
    @abc.abstractmethod
    def network(self):
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def gpu_preprocessor(self):
        raise NotImplementedError

    def compute_loss(self, experience, next_obs):
        # estimate value of next state
        with torch.no_grad():
            next_obs_on_device = self.gpu_preprocessor(next_obs, self.device)
            results, _ = self.network(next_obs_on_device, self.internals)
            last_values = results['critic'].squeeze(1).data

        # compute nstep return and advantage over batch
        batch_values = torch.stack(experience.values)
        value_targets, batch_advantages = self._compute_returns_advantages(
            batch_values, last_values, experience.rewards, experience.terminals
        )

        # batched value loss
        value_loss = 0.5 * torch.mean((value_targets - batch_values).pow(2))

        # normalize advantage so that an even number
        # of actions are reinforced and penalized
        if self.normalize_advantage:
            batch_advantages = (batch_advantages - batch_advantages.mean()) \
                               / (batch_advantages.std() + 1e-5)
        policy_loss = 0.
        entropy_loss = 0.

        rollout_len = len(experience.rewards)
        for i in range(rollout_len):
            log_probs = experience.log_probs[i]
            entropies = experience.entropies[i]

            policy_loss = policy_loss - (
                    log_probs * batch_advantages[i].unsqueeze(1).data
            ).sum(1)
            entropy_loss = entropy_loss - (
                    self.entropy_weight * entropies
            ).sum(1)

        batch_size = policy_loss.shape[0]
        nb_action = log_probs.shape[1]

        denom = batch_size * rollout_len * nb_action
        policy_loss = policy_loss.sum(0) / denom
        entropy_loss = entropy_loss.sum(0) / denom

        losses = {
            'value_loss': value_loss,
            'policy_loss': policy_loss,
            'entropy_loss': entropy_loss
        }
        metrics = {}
        return losses, metrics

    def _compute_returns_advantages(
            self, values, estimated_value, rewards, terminals
    ):
        if self.gae:
            gae = 0.
            gae_advantages = []

        next_value = estimated_value
        # First step of nstep reward target is estimated value of t+1
        target_return = estimated_value
        nstep_target_returns = []
        for i in reversed(range(len(rewards))):
            reward = rewards[i]
            terminal = terminals[i]

            # Nstep return is always calculated for the critic's target
            # using the GAE target for the critic results in the
            # same or worse performance
            target_return = reward + self.discount * target_return * terminal
            nstep_target_returns.append(target_return)

            # Generalized Advantage Estimation
            if self.gae:
                delta_t = reward \
                          + self.discount * next_value * terminal \
                          - values[i].data
                gae = gae * self.discount * self.tau * terminal + delta_t
                gae_advantages.append(gae)
                next_value = values[i].data

        # reverse lists
        nstep_target_returns = torch.stack(
            list(reversed(nstep_target_returns))
        ).data

        if self.gae:
            advantages = torch.stack(list(reversed(gae_advantages))).data
        else:
            advantages = nstep_target_returns - values.data

        return nstep_target_returns, advantages


class ActorCriticLearner(ActorCriticLearnerMixin):

    def __init__(self, network, gpu_preprocessor):
        self._network = network
        self._gpu_preprocessor = gpu_preprocessor

    @property
    def network(self):
        return self._network

    @property
    def gpu_preprocessor(self):
        return self._gpu_preprocessor
