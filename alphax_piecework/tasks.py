from alphax_piecework.engine import edge


def hourly():
	edge.process_queued_events()


def daily():
	edge.auto_submit_edge_logs()
	edge.purge_processed_events()
