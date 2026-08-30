import os
import logging
from pydantic import BaseModel, Field

# Base directory of the project
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class Settings(BaseModel):
    # Configurable directories. They default to subdirectories of BASE_DIR
    # but can be customized or set to absolute paths.
    OUTPUT_DIR: str = Field(default=os.path.join(BASE_DIR, "output"))
    TEMP_DIR: str = Field(default=os.path.join(BASE_DIR, "temp"))
    ASSETS_DIR: str = Field(default=os.path.join(BASE_DIR, "assets"))
    MUSIC_DIR: str = Field(default=os.path.join(BASE_DIR, "assets", "music"))
    LOGS_DIR: str = Field(default=os.path.join(BASE_DIR, "logs"))

    # Default server options
    HOST: str = Field(default="127.0.0.1")
    PORT: int = Field(default=8000)

    # Config JSON file path
    CONFIG_FILE: str = Field(default=os.path.join(BASE_DIR, "config.json"))

    FALLBACK_VIDEOS: dict = Field(default={
        "tech": [
            "https://raw.githubusercontent.com/intel-iot-devkit/sample-videos/master/classroom.mp4",
            "https://www.w3schools.com/html/mov_bbb.mp4"
        ],
        "history": [
            "https://www.w3schools.com/html/movie.mp4",
            "https://raw.githubusercontent.com/intel-iot-devkit/sample-videos/master/classroom.mp4"
        ],
        "how_why": [
            "https://www.w3schools.com/html/mov_bbb.mp4",
            "https://www.w3schools.com/html/movie.mp4"
        ]
    })

    def get_keys(self) -> dict:
        """Load API keys from local configuration file."""
        import json
        if os.path.exists(self.CONFIG_FILE):
            try:
                with open(self.CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    return {
                        "gemini_key": data.get("gemini_key", ""),
                        "pexels_key": data.get("pexels_key", "")
                    }
            except Exception as e:
                logger.error(f"Error reading config.json: {str(e)}")
        return {"gemini_key": "", "pexels_key": ""}

    def save_keys(self, gemini_key: str, pexels_key: str) -> None:
        """Save API keys to local configuration file."""
        import json
        try:
            with open(self.CONFIG_FILE, "w") as f:
                json.dump({
                    "gemini_key": gemini_key,
                    "pexels_key": pexels_key
                }, f, indent=4)
            logger.info("API keys successfully updated and saved to config.json")
        except Exception as e:
            logger.error(f"Failed to write keys to config.json: {str(e)}")
            raise e

    def create_directories(self) -> None:
        """Create configured folders if they do not exist."""
        for path in [self.OUTPUT_DIR, self.TEMP_DIR, self.ASSETS_DIR, self.MUSIC_DIR, self.LOGS_DIR]:
            os.makedirs(path, exist_ok=True)

    def setup_logging(self) -> logging.Logger:
        """Configure logging to output to both console and a file."""
        os.makedirs(self.LOGS_DIR, exist_ok=True)
        log_file = os.path.join(self.LOGS_DIR, "app.log")
        
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_file, encoding="utf-8")
            ]
        )
        logger = logging.getLogger("YouTubeAutomation")
        logger.info("Logging has been initialized. Logs are stored in %s", log_file)
        return logger

# Initialize standard settings
settings = Settings()
# Initialize standard logging
logger = settings.setup_logging()
# Ensure directories exist upon import
settings.create_directories()

