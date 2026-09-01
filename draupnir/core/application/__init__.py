"""Application layer: knows the workflow.

Every state change passes through the orchestrator in one transaction: guard,
act, write ledger, project. SAD 11B.
"""
