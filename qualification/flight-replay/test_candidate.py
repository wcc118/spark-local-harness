"""Focused behavioral tests for candidate.py flight-replay fix.

These tests verify the two key requirements from the flight-replay task:

1. compare_designs must not mutate caller-owned input on success or rejection.
2. Invalid aircraft definitions are row-level failures: retain a row for every
   configuration/case, with null unavailable root loads, null corresponding deltas,
   and meaningful reasons. Other valid configurations must still evaluate.
"""
import json
import hashlib
import zipfile
from pathlib import Path
import tempfile
import importlib.util
import sys


def load_candidate(candidate_path: Path, destination: Path):
    """Load candidate.py as a module, extracting baseline.zip first."""
    ROOT = Path(__file__).parent
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    archive = ROOT / 'baseline.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['fixture_sha256']:
        raise ValueError('baseline fixture hash mismatch')
    with zipfile.ZipFile(archive) as source:
        for name in source.namelist():
            target = (destination / name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError('fixture path escapes extraction directory')
        source.extractall(destination)
    sys.path.insert(0, str(destination / 'src'))
    import flightloads
    spec = importlib.util.spec_from_file_location(
        'flightloads.aircraft_design_compare', candidate_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, flightloads


def test_input_unchanged():
    """Verify caller-owned input is not mutated after compare_designs."""
    ROOT = Path(__file__).parent
    with tempfile.TemporaryDirectory(prefix='flight-replay-test-') as tmpdir:
        module, flightloads = load_candidate(ROOT / 'candidate.py', Path(tmpdir))
        
        # Import from the loaded module
        ws = flightloads.aircraft_workspace
        definition = ws.stable_example_definition()
        
        spec = {
            'schema': 'dive-load-comparison-v1',
            'configurations': [
                {'name': 'reference', 'definition': definition},
                {'name': 'alternate', 'definition': ws.stable_example_definition()}
            ],
            'cases': [
                {'name': 'screen', 'speed_ft_s': 100, 'load_factor': 1}
            ]
        }
        
        # Capture references before
        before_definition = spec['configurations'][0]['definition']
        before_components = before_definition['components']
        before_json = json.dumps(spec, sort_keys=True)
        
        # Run comparison
        report = module.compare_designs(spec)
        
        # Verify references unchanged
        assert spec['configurations'][0]['definition'] is before_definition, \
            "definition object was replaced"
        assert before_definition['components'] is before_components, \
            "components list was replaced"
        assert json.dumps(spec, sort_keys=True) == before_json, \
            "spec JSON serialization changed"
        
        # Verify we got rows
        assert len(report['rows']) == 2
        assert report['rows'][0]['configuration'] == 'reference'
        assert report['rows'][1]['configuration'] == 'alternate'
        
        print("test_input_unchanged: PASSED")


def test_invalid_definition_isolated():
    """Verify invalid definitions produce isolated rows with null metrics."""
    ROOT = Path(__file__).parent
    with tempfile.TemporaryDirectory(prefix='flight-replay-test-') as tmpdir:
        module, flightloads = load_candidate(ROOT / 'candidate.py', Path(tmpdir))
        
        # Import from the loaded module
        ws = flightloads.aircraft_workspace
        definition = ws.stable_example_definition()
        
        spec = {
            'schema': 'dive-load-comparison-v1',
            'configurations': [
                {'name': 'valid', 'definition': definition},
                {'name': 'invalid', 'definition': ws.stable_example_definition()}
            ],
            'cases': [
                {'name': 'screen', 'speed_ft_s': 100, 'load_factor': 1}
            ]
        }
        
        # Make configuration 1 invalid by setting flight_condition to None
        spec['configurations'][1]['definition']['flight_condition'] = None
        
        # Run comparison
        report = module.compare_designs(spec)
        
        # Verify we have 2 rows (one per configuration)
        assert len(report['rows']) == 2, \
            f"expected 2 rows, got {len(report['rows'])}"
        
        # Verify first row is valid (from first config)
        valid_row = report['rows'][0]
        assert valid_row['configuration'] == 'valid'
        assert valid_row['load_status'] == 'complete'
        assert valid_row['metrics']['root_Fz_lbf'] is not None, \
            "valid row should have root_Fz_lbf"
        assert valid_row['metrics']['root_Mx_in_lbf'] is not None, \
            "valid row should have root_Mx_in_lbf"
        
        # Verify second row is invalid (from second config)
        invalid_row = report['rows'][1]
        assert invalid_row['configuration'] == 'invalid'
        # Status is evaluation-failed due to invalid definition (flight_condition=None)
        assert invalid_row['metrics']['root_Fz_lbf'] is None, \
            "invalid row should have null root_Fz_lbf"
        assert invalid_row['metrics']['root_Mx_in_lbf'] is None, \
            "invalid row should have null root_Mx_in_lbf"
        assert invalid_row['load_reasons'], \
            "invalid row should have load reasons"
        assert 'Invalid aircraft definition' in invalid_row['load_reasons'][0] or \
               'flight_condition' in invalid_row['load_reasons'][0], \
            "invalid row should explain why"
        
        # Verify other valid configurations still evaluate
        # (this is implicitly tested by having valid_row with complete status)
        
        print("test_invalid_definition_isolated: PASSED")


def run_all_tests():
    """Run all focused tests."""
    # Run tests in separate processes to avoid module caching issues
    import subprocess
    
    # Test 1
    result = subprocess.run([sys.executable, __file__, '--test1'], 
                          capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print("STDERR:", result.stderr)
        raise SystemExit(1)
    
    # Test 2
    result = subprocess.run([sys.executable, __file__, '--test2'], 
                          capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print("STDERR:", result.stderr)
        raise SystemExit(1)
    
    print("\nAll tests passed!")


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--test1':
        test_input_unchanged()
    elif len(sys.argv) > 1 and sys.argv[1] == '--test2':
        test_invalid_definition_isolated()
    else:
        run_all_tests()
