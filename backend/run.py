import os

from dotenv import load_dotenv

load_dotenv()  # must run before the app reads its config

from app import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))
