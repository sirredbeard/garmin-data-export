"""Garmin Connect data for daily use by a personal agent.

The store comes first. Sync writes plain JSON files, one per day and
one per activity, plus a small summary for each day. Every other
command -- show, activities, weight, export -- reads the store and
never touches the network unless it is told to sync first.
"""

__version__ = "0.0.3"
