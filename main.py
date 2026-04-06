import uvicorn
from utils.app_factory import create_app
from utils.app_settings import settings

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, **settings.uvicorn_kwargs())
