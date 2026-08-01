from onenote.graph_client import GraphClient

client = GraphClient()
client.authenticate()

notebook = client.get_or_create_notebook("InstaBrain")

print(notebook["displayName"])
print(notebook["links"]["oneNoteWebUrl"]["href"])