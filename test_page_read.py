import requests

from onenote.graph_client import GRAPH_BASE_URL, GraphClient


TARGET_NOTEBOOK = "InstaBrain"
TARGET_SECTION = "Programming"
TARGET_TITLE = "Test OneNote Page"


client = GraphClient()
client.authenticate()

notebook = client.get_or_create_notebook(TARGET_NOTEBOOK)
sections = client.list_sections(notebook["id"])

section = None

for item in sections.get("value", []):
    if item.get("displayName") == TARGET_SECTION:
        section = item
        break

if section is None:
    raise Exception(f"Section not found: {TARGET_SECTION}")

pages_response = requests.get(
    f"{GRAPH_BASE_URL}/me/onenote/sections/{section['id']}/pages",
    headers=client._headers()
)

if pages_response.status_code != 200:
    raise Exception(pages_response.text)

matching_pages = []

for page in pages_response.json().get("value", []):
    if page.get("title") == TARGET_TITLE:
        matching_pages.append(page)

if not matching_pages:
    raise Exception(f"Page not found: {TARGET_TITLE}")

target_page = max(
    matching_pages,
    key=lambda item: item.get("createdDateTime") or ""
)
page_id = target_page["id"]

print("Page ID:", page_id)
print("title:", target_page.get("title"))
print("createdDateTime:", target_page.get("createdDateTime"))
print("createdByAppId:", target_page.get("createdByAppId"))
print("contentUrl:", target_page.get("contentUrl"))

page_response = requests.get(
    f"{GRAPH_BASE_URL}/me/onenote/pages/{page_id}",
    headers=client._headers()
)

content_response = requests.get(
    f"{GRAPH_BASE_URL}/me/onenote/pages/{page_id}/content",
    headers=client._headers()
)

preview_response = requests.get(
    f"{GRAPH_BASE_URL}/me/onenote/pages/{page_id}/preview",
    headers=client._headers()
)

print()
print("Page entity status code:", page_response.status_code)

print()
print("Page content status code:", content_response.status_code)
print(content_response.text[:1500])

print()
print("Page preview status code:", preview_response.status_code)
print(preview_response.text)
