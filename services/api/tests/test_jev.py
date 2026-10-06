"""Isolated provider-boundary tests; never written as live Jev evidence."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from services.api import jev

def response():
    return {'model': 'jev-1.13.0', 'answers': {
        'next': {'type': 'choice', 'choice': 'baseline', 'confidence': .8, 'probabilities': {'baseline': .9, 'finish': .1}},
        'risk': {'type': 'noul', 'noul': .7}}, 'usage': {'input_tokens': 12, 'output_tokens': 4}}

class JevTests(unittest.TestCase):
    def test_absent_credentials_never_make_provider_request(self):
        with patch.object(jev, 'credentials', return_value=''), patch.object(jev, 'urlopen') as request:
            with self.assertRaisesRegex(ValueError, 'API Key'):
                jev.evaluate({}, {'baseline': 'Baseline'})
            request.assert_not_called()

    def test_real_response_shape_keeps_probabilities_and_excludes_key(self):
        with patch.object(jev, 'urlopen', return_value=io.BytesIO(json.dumps(response()).encode())) as request:
            result = jev.evaluate({'remaining_simulations': 2}, {'baseline': 'Baseline', 'finish': 'Finish'}, 'isolated-secret')
            self.assertEqual(result['answers']['next']['choice'], 'baseline')
            self.assertEqual(result['answers']['risk']['noul'], .7)
            self.assertNotIn('isolated-secret', json.dumps(result))
            self.assertEqual(request.call_args.args[0].full_url, jev.ENDPOINT)
            self.assertEqual(request.call_args.args[0].get_header('Authorization'), 'Bearer isolated-secret')

    def test_invalid_or_unoffered_decisions_are_rejected(self):
        for changes in ({'choice': 'unoffered'}, {'probabilities': {'baseline': 2, 'finish': -1}}, {'confidence': float('nan')}):
            value = response(); value['answers']['next'].update(changes)
            with patch.object(jev, 'urlopen', return_value=io.BytesIO(json.dumps(value).encode())):
                with self.assertRaisesRegex(ValueError, '格式无效'):
                    jev.evaluate({}, {'baseline': 'Baseline', 'finish': 'Finish'}, 'isolated-secret')

    def test_failed_connection_does_not_replace_saved_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'credentials.json'; path.write_text(json.dumps({'api_key': 'old-secret'}))
            with patch.object(jev, 'CONFIG', path), patch.object(jev, 'evaluate', side_effect=ValueError('failed')):
                with self.assertRaises(ValueError):
                    jev.configure('new-secret')
                self.assertEqual(json.loads(path.read_text())['api_key'], 'old-secret')

if __name__ == '__main__':
    unittest.main()
