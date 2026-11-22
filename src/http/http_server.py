import logging
import click
from fastapi import FastAPI
import uvicorn
from src.http.routes.routeable import routeable
from src import utils
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger

log = get_topic_logger(LogTopic.STARTUP)

# The web server writes to the window itself, not through Mantella's log: like the other programs, only its warnings and errors.
# Without it, every request (each event on the settings page) is a line. The game's requests show under Game connection
WEB_SERVER_LOG_LEVEL = "warning"

class http_server:
    """A simple http server using FastAPI. Can be started using different routes.
    """
    def __init__(self) -> None:
        self.__app = FastAPI()

        ### Deactivate the logging to console by FastAPI
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)

        def secho(text, file=None, nl=None, err=None, color=None, **styles):
            pass

        def echo(text, file=None, nl=None, err=None, color=None, **styles):
            pass

        click.echo = echo
        click.secho = secho
        ### End of deactivate logging

    @property
    def app(self) -> FastAPI:
        return self.__app

    def _setup_routes(self, routes: list[routeable]):
        """Sets up the provided server routes
        
        Args:
            routes (list[routeable]): The list of routes to set up
        """
        for route in routes:
            route.add_route_to_server(self.__app)
        return self.__app

    def start(self, port: int, routes: list[routeable], play_startup_sound: bool):
        """Starts the server and sets up the provided routes

        Args:
            routes (list[routeable]): The list of routes to start
        """
        self._setup_routes(routes)

        if play_startup_sound:
            utils.play_mantella_ready_sound()
        
        log.info('Conversations not starting when you select an NPC? See here: https://art-from-the-machine.github.io/Mantella/pages/issues_qna')
        log.info('Waiting for player to select an NPC...')
    
        uvicorn.run(self.__app, port=port, log_level=WEB_SERVER_LOG_LEVEL)
