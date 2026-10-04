from scripts.evaluate_ball_tracks import evaluate


def test_evaluation_distinguishes_candidates_observations_inference_and_background_errors():
    data = {'frames': [
        {'t': 0, 'ball': {'x': 90, 'y': 90}, 'ballCandidates': [{'x': 10, 'y': 10}]},
        {'t': .2, 'ball': {'x': 20, 'y': 10, 'inferred': True}, 'ballCandidates': []},
        {'t': .4, 'ball': {'x': 30, 'y': 10}, 'ballCandidates': [{'x': 30, 'y': 10}]},
    ]}
    labels = {'tolerancePixels': 3, 'labels': [
        {'t': 0, 'x': 10, 'y': 10}, {'t': .2, 'x': 20, 'y': 10}, {'t': .4, 'x': 30, 'y': 10},
    ], 'negativeRegions': [{'t': 0, 'box': [85, 85, 95, 95]}]}
    score = evaluate(data, labels)
    assert score['matched'] == 2
    assert score['observedMatched'] == 1 and score['inferredMatched'] == 1
    assert score['candidateMatches'] == 2 and score['wrongLocations'] == 1
    assert score['negativeRegionHits'] == 1
    for f in data['frames']:
        del f['ballCandidates']
    assert evaluate(data, labels)['candidateMatches'] is None
