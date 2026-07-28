from onenote.graph_client import GraphClient

client = GraphClient()
client.authenticate()

profile = client.get_profile()

print(profile)