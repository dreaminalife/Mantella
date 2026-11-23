from src.http.http_server import http_server
import traceback
from src.http.routes.routeable import routeable
from src.http.routes.mantella_route import mantella_route
from src.http.routes.config_route import config_route
from src.http.routes.voice_files_route import voice_files_route
from src.setup import MantellaSetup
from src.logs.log_topic import LogTopic
from src.logs.topic_logger import get_topic_logger
from src.ui.start_ui import StartUI
from src.ui.ui_files_folder import use_own_ui_files_folder

MANTELLA_VERSION = '1.0'

def main():
    try:
        config, language_info = MantellaSetup().initialise(
            config_file='config.ini',
            logging_file='logging.log', 
            language_file='data/language_support.csv',
            version=MANTELLA_VERSION)

        mantella_http_server = http_server()

        conversation = mantella_route(
            config=config, 
            stt_secret_key_file='STT_SECRET_KEY.txt', 
            image_secret_key_file='IMAGE_SECRET_KEY.txt', 
            secret_key_file='GPT_SECRET_KEY.txt', 
            language_info=language_info
        )
        conversation.start_loading_in_background()
        use_own_ui_files_folder() # before the page is built: test sounds from the last run are removed
        ui = StartUI(config)
        config_reload = config_route(config)
        routes: list[routeable] = [conversation, ui, voice_files_route(config)]
            
        #add the UI
        mantella_http_server.start(int(config.port), routes, config.play_startup_sound)

    except Exception as e:
        get_topic_logger(LogTopic.STARTUP).error("".join(traceback.format_exception(e)))
        input("Press Enter to exit.")

if __name__ == '__main__':
    main()
