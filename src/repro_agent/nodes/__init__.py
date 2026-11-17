"""Graph nodes. Each node is a function that receives the state and returns the keys it changes.

Nodes that call the LLM receive the model as an extra argument; graph.py binds it.
"""
