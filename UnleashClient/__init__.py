from datetime import datetime, timezone
from typing import Dict
from fcache.cache import FileCache
from apscheduler.job import Job
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from UnleashClient.api import register_client
from UnleashClient.periodic_tasks import fetch_and_load_features, aggregate_and_send_metrics
from UnleashClient.strategies import ApplicationHostname, Default, GradualRolloutRandom, \
    GradualRolloutSessionId, GradualRolloutUserId, UserWithId, RemoteAddress
from UnleashClient.constants import METRIC_LAST_SENT_TIME
from .utils import LOGGER


# pylint: disable=dangerous-default-value
class UnleashClient():
    """
    Client implementation.

    """
    def __init__(self,
                 url: str,
                 app_name: str,
                 environment: str = "default",
                 instance_id: str = "unleash-client-python",
                 refresh_interval: int = 15,
                 metrics_interval: int = 60,
                 disable_metrics: bool = False,
                 disable_registration: bool = False,
                 custom_headers: dict = {},
                 custom_strategies: dict = {},
                 cache_directory: str = None) -> None:
        """
        A client for the Unleash feature toggle system.

        :param url: URL of the unleash server, required.
        :param app_name: Name of the application using the unleash client, required.
        :param environment: Name of the environment using the unleash client, optinal & defaults to "default".
        :param instance_id: Unique identifier for unleash client instance, optional & defaults to "unleash-client-python"
        :param refresh_interval: Provisioning refresh interval in ms, optional & defaults to 15 seconds
        :param metrics_interval: Metrics refresh interval in ms, optional & defaults to 60 seconds
        :param disable_metrics: Disables sending metrics to unleash server, optional & defaults to false.
        :param custom_headers: Default headers to send to unleash server, optional & defaults to empty.
        :param custom_strategies: Dictionary of custom strategy names : custom strategy objects
        :param cache_directory: Location of the cache directory. When unset, FCache will determine the location
        """
        # Configuration
        self.unleash_url = url.rstrip('\\')
        self.unleash_app_name = app_name
        self.unleash_environment = environment
        self.unleash_instance_id = instance_id
        self.unleash_refresh_interval = refresh_interval
        self.unleash_metrics_interval = metrics_interval
        self.unleash_disable_metrics = disable_metrics
        self.unleash_disable_registration = disable_registration
        self.unleash_custom_headers = custom_headers
        self.unleash_static_context = {
            "appName": self.unleash_app_name,
            "environment": self.unleash_environment
        }

        # Class objects
        self.cache = FileCache(self.unleash_instance_id, app_cache_dir=cache_directory)
        self.features = {}  # type: Dict
        self.scheduler = BackgroundScheduler()
        self.fl_job = None  # type: Job
        self.metric_job = None  # type: Job
        self.cache[METRIC_LAST_SENT_TIME] = datetime.now(timezone.utc)
        self.cache.sync()

        # Mappings
        default_strategy_mapping = {
            "applicationHostname": ApplicationHostname,
            "default": Default,
            "gradualRolloutRandom": GradualRolloutRandom,
            "gradualRolloutSessionId": GradualRolloutSessionId,
            "gradualRolloutUserId": GradualRolloutUserId,
            "remoteAddress": RemoteAddress,
            "userWithId": UserWithId
        }

        self.strategy_mapping = {**custom_strategies, **default_strategy_mapping}

        # Client status
        self.is_initialized = False

    def initialize_client(self) -> None:
        """
        Initializes client and starts communication with central unleash server(s).

        This kicks off:
        * Client registration
        * Provisioning poll
        * Stats poll

        :return:
        """
        # Setup
        fl_args = {
            "url": self.unleash_url,
            "app_name": self.unleash_app_name,
            "instance_id": self.unleash_instance_id,
            "custom_headers": self.unleash_custom_headers,
            "cache": self.cache,
            "features": self.features,
            "strategy_mapping": self.strategy_mapping
        }

        metrics_args = {
            "url": self.unleash_url,
            "app_name": self.unleash_app_name,
            "instance_id": self.unleash_instance_id,
            "custom_headers": self.unleash_custom_headers,
            "features": self.features,
            "ondisk_cache": self.cache
        }

        # Register app
        if not self.unleash_disable_registration:
            register_client(self.unleash_url, self.unleash_app_name, self.unleash_instance_id,
                            self.unleash_metrics_interval, self.unleash_custom_headers, self.strategy_mapping)

        fetch_and_load_features(**fl_args)

        # Start periodic jobs
        self.scheduler.start()
        self.fl_job = self.scheduler.add_job(fetch_and_load_features,
                                             trigger=IntervalTrigger(seconds=int(self.unleash_refresh_interval)),
                                             kwargs=fl_args)

        if not self.unleash_disable_metrics:
            self.metric_job = self.scheduler.add_job(aggregate_and_send_metrics,
                                                     trigger=IntervalTrigger(seconds=int(self.unleash_metrics_interval)),
                                                     kwargs=metrics_args)

        self.is_initialized = True

    def destroy(self):
        """
        Gracefully shuts down the Unleash client by stopping jobs, stopping the scheduler, and deleting the cache.

        You shouldn't need this too much!

        :return:
        """
        self.fl_job.remove()
        if self.metric_job:
            self.metric_job.remove()
        self.scheduler.shutdown()
        self.cache.delete()

    # pylint: disable=broad-except
    def is_enabled(self,
                   feature_name: str,
                   context: dict = {},
                   default_value: bool = False) -> bool:
        """
        Checks if a feature toggle is enabled.

        Notes:
        * If client hasn't been initialized yet or an error occurs, flat will default to false.

        :param feature_name: Name of the feature
        :param context: Dictionary with context (e.g. IPs, email) for feature toggle.
        :param default_value: Allows override of default value.
        :return: True/False
        """
        context.update(self.unleash_static_context)

        if self.is_initialized:
            try:
                return self.features[feature_name].is_enabled(context, default_value)
            except Exception as excep:
                LOGGER.warning("Returning default value for feature: %s", feature_name)
                LOGGER.warning("Error checking feature flag: %s", excep)
                return default_value
        else:
            LOGGER.warning("Returning default value for feature: %s", feature_name)
            LOGGER.warning("Attempted to get feature_flag %s, but client wasn't initialized!", feature_name)
            return default_value
