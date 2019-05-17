import typing
from pathlib import Path

import pytest
from compose.cli.command import project_from_options
from compose.container import Container
from compose.project import Project
from compose.service import ImageType


class ContainerAlreadyExist(Exception):
    """Raised when running containers are found during docker compose up"""
    pass


__all__ = [
    "DockerComposePlugin",
    "NetworkInfo",
    "generate_scoped_network_info_fixture",
    "generate_scoped_containers_fixture",
    "plugin",
]


class NetworkInfo:
    def __init__(self, container_port: typing.Text,
                 hostname: typing.Text, host_port: int,):
        """
        Container for info about how to connect to a service exposed by a
        Docker container.

        :param container_port: Port (and usually also protocol name) exposed
        internally on the container.
        :param hostname: Hostname to use when accessing this service.
        :param host_port: Port number to use when accessing this service.
        """
        self.container_port = container_port
        self.hostname = hostname
        self.host_port = host_port


class DockerComposePlugin:
    """
    Integrates docker-compose into pytest integration tests.
    """

    # noinspection SpellCheckingInspection
    @staticmethod
    def pytest_addoption(parser):
        """
        Adds custom options to the ``pytest`` command.

        https://docs.pytest.org/en/latest/writing_plugins.html#_pytest.hookspec.pytest_addoption
        """
        group = parser.getgroup("docker_compose", "integration tests")

        group.addoption(
            "--docker-compose",
            dest="docker_compose",
            default=".",
            help="Path to docker-compose.yml file, or directory containing same.",
        )

        group.addoption("--docker-compose-no-build", action="store_true",
                        default=False, help="Boolean to not build docker containers")

    @pytest.fixture
    def docker_containers(self, docker_project: Project):
        """
        Spins up a the containers for the Docker project and returns
        them.

        Note that this fixture's scope is a single test; the containers
        will be stopped after the test is finished.

        This is intentional; stopping the containers destroys local
        storage, so that the next test can start with fresh containers.
        """
        containers = self._containers_up(docker_project)

        yield containers

        self._containers_down(docker_project, containers)

    @pytest.fixture
    def docker_network_info(self, docker_containers: typing.List[Container]):
        """
        Returns hostnames and exposed port numbers for each container,
        so that tests can interact with them.
        """
        return self._extract_network_info(docker_containers)

    @pytest.fixture(scope="session")
    def docker_project(self, request):
        """
        Builds the Docker project if necessary, once per session.

        Returns the project instance, which can be used to start and stop
        the Docker containers.
        """
        docker_compose = Path(request.config.getoption("docker_compose"))

        if docker_compose.is_dir():
            docker_compose /= "docker-compose.yml"

        if not docker_compose.is_file():
            raise ValueError(
                "Unable to find `{docker_compose}` "
                "for integration tests.".format(
                    docker_compose=docker_compose.absolute(),
                ),
            )

        project = project_from_options(
            project_dir=str(docker_compose.parent),
            options={"--file": [docker_compose.name]},
        )

        if not request.config.getoption("--docker-compose-no-build"):
            project.build()

        return project

    @classmethod
    def _containers_up(cls, docker_project: Project) -> typing.List[Container]:
        """
        Brings up all containers in the specified project.
        """
        if any(docker_project.containers()):
            raise ContainerAlreadyExist(
                'pytest-docker-compose tried to start containers but there are'
                ' already running containers: %s, you probably scoped your'
                ' tests wrong' % docker_project.containers())
        containers = docker_project.up()  # type: typing.List[Container]

        if not containers:
            raise ValueError("`docker-compose` didn't launch any containers!")

        return containers

    @staticmethod
    def _containers_down(docker_project: Project,
                         docker_containers: typing.Iterable[Container]) -> None:
        """
        Brings down containers that were launched using :py:meth:`_containers_up`.
        """
        # Send container logs to stdout, so that they get included in
        # the test report.
        # https://docs.pytest.org/en/latest/capture.html
        for container in sorted(docker_containers, key=lambda c: c.name):
            header = "Logs from {name}:".format(name=container.name)
            print(header)
            print("=" * len(header))
            print(
                container.logs().decode("utf-8", errors="replace") or
                "(no logs)"
            )
            print()

        docker_project.down(ImageType.none, False)

    @staticmethod
    def _extract_network_info(docker_containers: typing.Iterable[Container], docker_project: Project
    ) -> typing.Dict[str, typing.List[NetworkInfo]]:
        """
        Generates :py:class:`NetworkInfo` instances for each container and
        returns them in a dict of lists.
        """
        return {container.name: create_network_info_for_container(container)
                for container in docker_containers}


def create_network_info_for_container(container):
    """
    Generates :py:class:`NetworkInfo` instances corresponding to all available
    port bindings in a container
    """
    return [NetworkInfo(container_port=container_port,
                        hostname=port_config["HostIp"] or "localhost",
                        host_port=port_config["HostPort"],)
            for container_port, port_configs in
            container.ports.items()
            for port_config in port_configs]


def generate_scoped_containers_fixture(scope):
    @pytest.fixture(scope=scope)
    def scoped_containers_fixture(docker_project: Project):
        containers = DockerComposePlugin._containers_up(docker_project)
        for container in containers:
            container.network_info = create_network_info_for_container(container)
        yield {container.name: container for container in containers}
        DockerComposePlugin._containers_down(docker_project, containers)
    scoped_containers_fixture.__wrapped__.__doc__ = """
        Spins up the containers for the Docker project and returns them in a
        dictionary. Each container has one additional attribute called
        network_info to simplify accessing the hostnames and exposed port
        numbers for each container.
        This set of containers is scoped to '%s'
        """ % scope
    return scoped_containers_fixture


plugin = DockerComposePlugin()
