"""
Central storage paths for PocketProNYGames.

Every module resolves its on-disk locations through these helpers so one
environment variable moves all state. Defaults match the Docker volume layout
(/data), so containers behave exactly as before.

    DATA_DIR              base for all persistent state      (default /data)
    MODELS_DIR            trained model files                (default $DATA_DIR/models)
    PREDICTION_STATE_DIR  weights / readiness for the engine (default $DATA_DIR/prediction)
"""
import os


def data_dir() -> str:
    return os.environ.get("DATA_DIR", "/data")


def models_dir() -> str:
    return os.environ.get("MODELS_DIR") or os.path.join(data_dir(), "models")


def prediction_state_dir() -> str:
    return os.environ.get("PREDICTION_STATE_DIR") or os.path.join(data_dir(), "prediction")


def experiments_dir() -> str:
    return os.path.join(data_dir(), "experiments")


def experiments_file() -> str:
    return os.path.join(experiments_dir(), "experiments.json")


def ingestion_cache_dir() -> str:
    return os.path.join(data_dir(), "ingestion_cache")
