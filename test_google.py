import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(
    page_title="Google Sheets Test",
    page_icon="☁️"
)

st.title("☁️ Google Sheets Connection Test")

try:
    SERVICE_ACCOUNT_FILE = "nse-pro-trader-c9fdbcd4c2b1.json"

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=scopes
    )

    client = gspread.authorize(credentials)

    spreadsheet = client.open_by_url(
        "https://docs.google.com/spreadsheets/d/1qDl4eW5vKWr99kbXUHRaXh64FZGCAw8Q7I-hP2IOJLM/edit"
    )

    st.success("✅ Google Sheets connected successfully!")

    st.write("Spreadsheet name:")
    st.info(spreadsheet.title)

except Exception as e:
    st.error("❌ Connection failed")
    st.exception(e)