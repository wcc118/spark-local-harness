"""Dive/load comparison pilot: vertical-translation load screening at prescribed points.

This is NOT a dive trajectory simulation, aeroelastic prediction, strength assessment,
fatigue/buckling margin or flight clearance. This is prescribed-load equilibrium screening.
"""
from copy import deepcopy
import hashlib
import html
import json
import os
import sys
from numbers import Real

import numpy as np

from .aircraft_workspace import G_IN_S2, validate_definition, evaluate_aircraft
from .aircraft_structure import from_aircraft
from .aircraft_flight import validate_condition

# Output schema version
SCHEMA_VERSION = 'dive-load-comparison-report-v1'
INPUT_SCHEMA = 'dive-load-comparison-v1'

# Numerical pilot bounds (NOT certified flight limits)
SPEED_MIN = 30.0
SPEED_MAX = 300.0
LOAD_FACTOR_MIN = 0.0
LOAD_FACTOR_MAX = 6.0


def _validate_number(value, name, *, lo=None, hi=None, allow_none=False, allow_bool=True):
    """Validate a numeric value. Reject bools, nonfinite, or out-of-range."""
    if isinstance(value, bool) and not allow_bool:
        raise ValueError(f'{name} must not be a boolean')
    if value is None and allow_none:
        return None
    if not isinstance(value, Real) or isinstance(value, bool):
        raise ValueError(f'{name} must be a real number, got {type(value).__name__}')
    if not np.isfinite(value):
        raise ValueError(f'{name} must be finite (no NaN/Infinity)')
    if lo is not None and value < lo:
        raise ValueError(f'{name}={value} < {lo}')
    if hi is not None and value > hi:
        raise ValueError(f'{name}={value} > {hi}')
    return float(value)


def _validate_string(value, name, *, allow_none=False, max_length=200):
    """Validate a string value."""
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string')
    if len(value) > max_length:
        raise ValueError(f'{name} exceeds {max_length} characters')
    return value.strip()


def _validate_input_spec(spec):
    """Validate the outer input spec structure and return a deepcopy.

    Input schema has configurations as LIST of 2-4 {name, definition} objects,
    not a dict. Cases is list of 1-12 {name, speed_ft_s, load_factor} objects.
    """
    if not isinstance(spec, dict):
        raise ValueError('Input must be a JSON object')

    # Check required top-level keys
    if set(spec.keys()) != {'schema', 'configurations', 'cases'}:
        raise ValueError('Input must contain exactly: schema, configurations, cases')

    # Validate schema
    if spec.get('schema') != INPUT_SCHEMA:
        raise ValueError(f'schema must be "{INPUT_SCHEMA}"')

    # Validate configurations: must be LIST of 2-4 {name, definition} objects
    configs = spec.get('configurations')
    if not isinstance(configs, list) or len(configs) < 2 or len(configs) > 4:
        raise ValueError('configurations must be a list of 2-4 named aircraft definitions')

    seen_names = set()
    for i, cfg in enumerate(configs):
        if not isinstance(cfg, dict) or set(cfg.keys()) != {'name', 'definition'}:
            raise ValueError(f'configuration {i} must have exactly {{name, definition}} keys')
        cfg_name = _validate_string(cfg['name'], f'configuration {i} name')
        if cfg_name in seen_names:
            raise ValueError(f'duplicate configuration name: "{cfg_name}"')
        seen_names.add(cfg_name)
        # Deepcopy before validating definition to avoid mutation
        cfg['definition'] = deepcopy(cfg['definition'])
        validate_definition(cfg['definition'])

    # Validate cases: list of 1-12 {name, speed_ft_s, load_factor} objects
    cases = spec.get('cases')
    if not isinstance(cases, list) or len(cases) < 1 or len(cases) > 12:
        raise ValueError('cases must be a list of 1-12 screening cases')

    seen_case_names = set()
    for i, case in enumerate(cases):
        if not isinstance(case, dict) or set(case.keys()) != {'name', 'speed_ft_s', 'load_factor'}:
            raise ValueError(f'case {i} must have exactly: name, speed_ft_s, load_factor')
        case_name = _validate_string(case['name'], f'case {i} name')
        if case_name in seen_case_names:
            raise ValueError(f'duplicate case name: "{case_name}"')
        seen_case_names.add(case_name)

        case['speed_ft_s'] = _validate_number(
            case['speed_ft_s'], f'case {i} speed_ft_s', lo=SPEED_MIN, hi=SPEED_MAX)
        case['load_factor'] = _validate_number(
            case['load_factor'], f'case {i} load_factor', lo=LOAD_FACTOR_MIN, hi=LOAD_FACTOR_MAX)

    # Return deep copy to ensure caller data is never mutated
    return deepcopy(spec)


def _compute_definition_sha256(definition):
    """Compute SHA256 of original definition, immutable.

    Uses json.dumps with sort_keys and no NaN to ensure deterministic hash.
    Must not hash the modified per-case copy.
    """
    # Use json.dumps with sort_keys and no NaN to ensure deterministic hash
    digest = hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    ).hexdigest()
    return digest


def _copy_definition_with_load_case(definition, load_factor, speed_ft_s):
    """Return a deepcopy of definition with load_case and flight_condition set.

    Sets load_case={kind:'vertical-translation',a_up_g:n-1}
    Sets flight_condition['speed_ft_s']=speed_ft_s
    """
    d = deepcopy(definition)
    d['load_case'] = {
        'kind': 'vertical-translation',
        'a_up_g': load_factor - 1.0
    }
    # Only set speed if flight_condition exists (required)
    if 'flight_condition' in d:
        d['flight_condition'] = deepcopy(d['flight_condition'])
        d['flight_condition']['speed_ft_s'] = speed_ft_s
    return d


def _evaluate_configuration(definition, load_factor, speed_ft_s):
    """Evaluate aircraft for a specific load factor and speed.

    Returns dict with metrics, status, trim info, and reasons for any nulls.

    All nine metric keys are initialized to None for every row.
    Uses actual loads['balanced'] before reporting required/root/cut loads.
    Balance residuals copy loads['whole_force_residual_lbf'] and
    loads['whole_moment_residual_in_lbf'] directly.
    """
    # Initialize all nine metric keys to None
    metrics = {
        'weight_lbf': None,
        'cg_station_in': None,
        'dynamic_pressure_psi': None,
        'wing_up_lbf': None,
        'tail_up_lbf': None,
        'root_Fz_lbf': None,
        'root_Mx_in_lbf': None,
        'cut_Fz_lbf': None,
        'cut_My_in_lbf': None
    }

    result = {
        'metrics': metrics,
        'status': 'incomplete',
        'load_reasons': [],
        'trim': {'converged': False, 'feasible': False, 'domain_violations': []}
    }

    # Copy definition with load case and speed
    d = _copy_definition_with_load_case(definition, load_factor, speed_ft_s)

    # Evaluate aircraft - this validates and computes loads
    try:
        evaluation = evaluate_aircraft(d)
    except Exception as e:
        result['status'] = 'evaluation-failed'
        result['load_reasons'].append(f'Evaluation error: {e}')
        return result

    # Check if evaluation has flight_condition with predicted dynamic_pressure
    fc = evaluation.get('flight_condition', {}) or {}
    predicted = fc.get('predicted', {}) or {}
    if predicted and 'dynamic_pressure_psi' in predicted:
        metrics['dynamic_pressure_psi'] = predicted['dynamic_pressure_psi']

    # Check mass completeness
    if evaluation.get('mass_properties') is None:
        result['status'] = 'incomplete-mass'
        result['load_reasons'].append('Missing component mass data')
        trim_info = fc.get('trim', {}) or {}
        result['trim']['converged'] = trim_info.get('converged', False)
        result['trim']['feasible'] = trim_info.get('feasible', False)
        result['trim']['domain_violations'] = trim_info.get('domain_violations', [])
        return result

    # Check symmetry - longitudinal model only
    mp = evaluation['mass_properties']
    g = evaluation.get('geometry', {})
    if abs(mp.get('cg_B_in', [0, 0, 0])[1]) > 1e-8:
        result['status'] = 'unsupported-asymmetry'
        result['load_reasons'].append('Lateral CG offset not supported')
        return result
    if any(abs(g.get(f'wing_load_y_in', 0)) > 1e-8 for g in [g]):
        result['status'] = 'unsupported-asymmetry'
        result['load_reasons'].append('Wing load offset not on centerline')
        return result

    # Get mass properties
    metrics['weight_lbf'] = mp['weight_lbf']
    metrics['cg_station_in'] = -mp['cg_B_in'][0]  # cg_B_in[0] is -station

    # Get loads from evaluation
    loads = evaluation.get('loads')

    # Use actual loads['balanced'] before reporting required/root/cut loads
    if loads is None:
        result['status'] = 'unbalanced'
        result['load_reasons'].append('Global force/moment balance failed')
        return result

    # Extract required forces from loads only if balanced
    # loads being non-null alone is NOT balance evidence
    if loads.get('balanced'):
        wing_up_lbf = loads.get('wing_up_lbf')
        tail_up_lbf = loads.get('tail_up_lbf')

        metrics['wing_up_lbf'] = wing_up_lbf
        metrics['tail_up_lbf'] = tail_up_lbf

        # Evaluate wing beam for root reactions
        # This requires from_aircraft which checks structural feasibility
        wing_beam = None
        try:
            wing_beam = from_aircraft(d)
        except ValueError as e:
            # Unsupported wing geometry - wing root metrics are null
            result['load_reasons'].append(f'Unsupported wing geometry: {e}')

        # Calculate root reactions for uniform right panel
        # Right wing: length L, weight W_wing_right
        # Required: Fz = Lw/2 - n*W_right, Mx = Fz * (L/2)
        if wing_beam is not None:
            L_in = wing_beam.length_in  # half span
            W_right_lbf = wing_beam.weight_lbf

            # Root Fz: half of wing lift minus n times right wing weight
            Fz = wing_up_lbf / 2.0 - load_factor * W_right_lbf
            # Root Mx: Fz * half-span (for uniform load on cantilever)
            Mx = Fz * (L_in / 2.0)

            metrics['root_Fz_lbf'] = Fz
            metrics['root_Mx_in_lbf'] = Mx

            # Get cut resultants from evaluation if available
            # cut_force_B_lbf[2] and cut_moment_B_in_lbf[1] are ALREADY signed
            # body-axis reactions ON retained body BY discarded body
            if loads.get('cut_force_B_lbf') is not None and loads.get('cut_moment_B_in_lbf') is not None:
                # These are ALREADY signed correctly - DO NOT negate
                metrics['cut_Fz_lbf'] = loads['cut_force_B_lbf'][2]
                metrics['cut_My_in_lbf'] = loads['cut_moment_B_in_lbf'][1]

            result['status'] = 'complete'
        else:
            # Wing beam unavailable - null wing root metrics
            result['load_reasons'].append('Wing root reactions unavailable (unsupported geometry)')
    else:
        # Unbalanced - required reactions unavailable
        result['status'] = 'unbalanced'
        result['load_reasons'].append('Global force/moment balance failed')

    # Trim info from flight condition evaluation
    trim_info = fc.get('trim', {}) or {}
    result['trim']['converged'] = trim_info.get('converged', False)
    result['trim']['feasible'] = trim_info.get('feasible', False)
    result['trim']['domain_violations'] = trim_info.get('domain_violations', [])

    # Add balance residuals using whole_force_residual_lbf and whole_moment_residual_in_lbf
    # Do NOT add external force to mass*a (wrong sign), or mix retained-cut and whole-nose moments
    if loads:
        whole_force = loads.get('whole_force_residual_lbf')
        whole_moment = loads.get('whole_moment_residual_in_lbf')
        if whole_force is not None and whole_moment is not None:
            result['balance_residuals'] = {
                'whole_force_residual_lbf': whole_force,
                'whole_moment_residual_in_lbf': whole_moment
            }
        else:
            result['balance_residuals'] = None
    else:
        result['balance_residuals'] = None

    return result


def compare_designs(spec):
    """Compare multiple aircraft designs across screening cases.

    Args:
        spec: Input dict with schema, configurations (list of 2-4 {name, definition}),
              and cases (list of 1-12 {name, speed_ft_s, load_factor})

    Returns:
        Report dict with schema, baseline, assumptions, and rows
    """
    # Validate input spec - returns deepcopy, never mutates caller data
    validated_spec = _validate_input_spec(spec)

    # Build report structure
    report = {
        'schema': SCHEMA_VERSION,
        'baseline': {
            'first_configuration': validated_spec['configurations'][0]['name'],
            'first_case': validated_spec['cases'][0]['name']
        },
        'assumptions': [
            'Input bounds: speed 30..300 ft/s, load_factor 0..6 (numerical pilot bounds, NOT certified)',
            'Vertical translation model: n = 1 + a_up/g, equilibrium loads at prescribed n',
            'Dynamic pressure from evaluation flight_condition.predicted when available',
            'Wing root reactions: Fz = Lw/2 - n*W_right, Mx = Fz * (L/2) for uniform panel',
            'Right wing: half of exposed span, symmetric loading, body +z down',
            'Structural feasibility checked via from_aircraft; unsupported geometry yields null',
            'Aero feasibility and required equilibrium loads are separate claims',
            'Trim convergence does not imply structural validity or flight clearance',
            'No aerodynamic trim prediction into required equilibrium beam result',
            'Output is screening only; not a design acceptance or flight clearance'
        ],
        'rows': []
    }

    # Process each configuration in input order
    config_list = validated_spec['configurations']

    # Pre-compute baseline metrics for deltas
    # Delta compares FIRST CONFIGURATION at SAME CASE, never first case globally
    # First compute all baseline metrics by running first config against all cases
    baseline_metrics_by_case = {}
    first_definition = config_list[0]['definition']

    for case in validated_spec['cases']:
        case_name = case['name']
        speed_ft_s = case['speed_ft_s']
        load_factor = case['load_factor']

        # Evaluate first config at this case for baseline
        baseline_eval = _evaluate_configuration(first_definition, load_factor, speed_ft_s)
        baseline_metrics_by_case[case_name] = baseline_eval['metrics']

    # Now process all configs and cases
    for cfg in config_list:
        config_name = cfg['name']
        definition = cfg['definition']
        def_sha256 = _compute_definition_sha256(definition)

        for case in validated_spec['cases']:
            case_name = case['name']
            speed_ft_s = case['speed_ft_s']
            load_factor = case['load_factor']

            # Evaluate this configuration/case combination
            eval_result = _evaluate_configuration(definition, load_factor, speed_ft_s)

            # Build row
            row = {
                'configuration': config_name,
                'case': case_name,
                'definition_sha256': def_sha256,
                'speed_ft_s': speed_ft_s,
                'load_factor': load_factor,
                'metrics': eval_result['metrics'],
                'delta_from_baseline': {},
                'load_status': eval_result['status'],
                'load_reasons': eval_result['load_reasons'],
                'trim': {
                    'converged': eval_result['trim']['converged'],
                    'feasible': eval_result['trim']['feasible'],
                    'domain_violations': eval_result['trim']['domain_violations']
                }
            }

            # Add balance residuals to row
            row['balance_residuals'] = eval_result.get('balance_residuals')

            # Compute deltas from baseline (same case, first config)
            baseline_metrics = baseline_metrics_by_case[case_name]

            delta = {}
            for key in baseline_metrics:
                base_val = baseline_metrics.get(key)
                curr_val = eval_result['metrics'].get(key)

                if base_val is None or curr_val is None:
                    # Cannot compute delta if either is unavailable
                    delta[key] = None
                elif base_val == curr_val:
                    delta[key] = 0.0
                else:
                    delta[key] = curr_val - base_val

            row['delta_from_baseline'] = delta

            report['rows'].append(row)

    return report


def _escape_html(text):
    """Escape text for safe HTML output."""
    if text is None:
        return 'null'
    return html.escape(str(text))


def _format_number(value, decimals=6):
    """Format a number, handling None/NaN/Inf."""
    if value is None:
        return 'null'
    if not np.isfinite(value):
        return repr(value)
    return f'{value:.{decimals}f}'


def generate_html_report(report):
    """Generate a standalone HTML comparison table with escaped content.

    Displays load_status and load_reasons for each row.
    Shows unavailable values as null.
    Shows case-matched deltas.
    Shows signed units.
    Shows trim limits.
    Corrects My label (was Mx in reading index 1).
    """
    html_parts = []

    html_parts.append('<!DOCTYPE html>')
    html_parts.append('<html>')
    html_parts.append('<head>')
    html_parts.append('<meta charset="UTF-8">')
    html_parts.append('<title>Dive/Load Comparison Report</title>')
    html_parts.append('<style>')
    html_parts.append('body { font-family: Arial, sans-serif; margin: 20px; }')
    html_parts.append('h1 { color: #333; }')
    html_parts.append('.assumptions { background: #f5f5f5; padding: 10px; border-radius: 4px; }')
    html_parts.append('.assumptions ul { margin: 5px 0; }')
    html_parts.append('table { border-collapse: collapse; width: 100%; margin-top: 20px; }')
    html_parts.append('th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }')
    html_parts.append('th { background: #4a90d9; color: white; }')
    html_parts.append('tr:nth-child(even) { background: #f9f9f9; }')
    html_parts.append('.null { color: #888; font-style: italic; }')
    html_parts.append('.warn { color: #d9534f; }')
    html_parts.append('.pass { color: #5cb85c; }')
    html_parts.append('.delta { color: #666; font-size: 0.9em; }')
    html_parts.append('.reasons { font-size: 0.85em; color: #666; max-width: 200px; }')
    html_parts.append('</style>')
    html_parts.append('</head>')
    html_parts.append('<body>')

    # Title
    html_parts.append('<h1>Dive/Load Comparison Report</h1>')
    html_parts.append(f'<p>Report schema: {_escape_html(report["schema"])}</p>')

    # Baseline info
    html_parts.append('<h2>Baseline</h2>')
    html_parts.append('<ul>')
    html_parts.append(f'<li>Baseline configuration: {_escape_html(report["baseline"]["first_configuration"])}</li>')
    html_parts.append(f'<li>Baseline case: {_escape_html(report["baseline"]["first_case"])}</li>')
    html_parts.append('</ul>')

    # Assumptions
    html_parts.append('<h2>Assumptions</h2>')
    html_parts.append('<div class="assumptions">')
    html_parts.append('<ul>')
    for assumption in report['assumptions']:
        html_parts.append(f'<li>{_escape_html(assumption)}</li>')
    html_parts.append('</ul>')
    html_parts.append('</div>')

    # Table header
    html_parts.append('<h2>Comparison Rows</h2>')
    html_parts.append('<table>')
    html_parts.append('<thead>')
    html_parts.append('<tr>')
    html_parts.append('<th>Configuration</th>')
    html_parts.append('<th>Case</th>')
    html_parts.append('<th>Wt (lbf)</th>')
    html_parts.append('<th>CG (in)</th>')
    html_parts.append('<th>q (psi)</th>')
    html_parts.append('<th>Wing Lft (lbf)</th>')
    html_parts.append('<th>Tail Lft (lbf)</th>')
    html_parts.append('<th>Root Fz (lbf)</th>')
    html_parts.append('<th>Root My (in-lbf)</th>')
    html_parts.append('<th>Cut Fz (lbf)</th>')
    html_parts.append('<th>Cut My (in-lbf)</th>')
    html_parts.append('<th>Load Status</th>')
    html_parts.append('<th>Load Reasons</th>')
    html_parts.append('<th>Trim Conv.</th>')
    html_parts.append('<th>Trim Feas.</th>')
    html_parts.append('<th>Domain Violations</th>')
    html_parts.append('<th>Balance Resid Fz</th>')
    html_parts.append('<th>Balance Resid My</th>')
    html_parts.append('</tr>')
    html_parts.append('</thead>')
    html_parts.append('<tbody>')

    # Table rows
    for row in report['rows']:
        metrics = row['metrics']
        deltas = row['delta_from_baseline']

        html_parts.append('<tr>')
        html_parts.append(f'<td>{_escape_html(row["configuration"])}</td>')
        html_parts.append(f'<td>{_escape_html(row["case"])}</td>')

        # Metrics cells
        for key in ['weight_lbf', 'cg_station_in', 'dynamic_pressure_psi',
                    'wing_up_lbf', 'tail_up_lbf', 'root_Fz_lbf', 'root_Mx_in_lbf',
                    'cut_Fz_lbf', 'cut_My_in_lbf']:
            val = metrics.get(key)
            delta_val = deltas.get(key)

            html_parts.append('<td>')
            if val is not None:
                html_parts.append(f'<span class="pass">{_format_number(val)}</span>')
            else:
                html_parts.append('<span class="null">null</span>')
            html_parts.append('</td>')

        # Load status
        html_parts.append(f'<td>{_escape_html(row["load_status"])}</td>')

        # Load reasons - show all reasons if any
        reasons = row['load_reasons']
        if reasons:
            html_parts.append(f'<td class="reasons"><span class="warn">{_escape_html("; ".join(reasons))}</span></td>')
        else:
            html_parts.append('<td>-</td>')

        # Trim cells
        trim = row['trim']
        converged = 'Yes' if trim['converged'] else 'No'
        feasible = 'Yes' if trim['feasible'] else 'No'
        html_parts.append(f'<td>{_escape_html(converged)}</td>')
        html_parts.append(f'<td>{_escape_html(feasible)}</td>')

        # Domain violations
        dv = trim['domain_violations']
        if dv:
            html_parts.append(f'<td class="warn">{_escape_html("; ".join(dv))}</td>')
        else:
            html_parts.append('<td>-</td>')

        # Balance residuals
        br = row.get('balance_residuals')
        if br:
            fz_res = br.get('whole_force_residual_lbf', [None, None, None])
            my_res = br.get('whole_moment_residual_in_lbf', [None, None, None])
            fz_val = fz_res[2] if len(fz_res) > 2 else None
            my_val = my_res[1] if len(my_res) > 1 else None
            html_parts.append(f'<td>{_format_number(fz_val)}</td>')
            html_parts.append(f'<td>{_format_number(my_val)}</td>')
        else:
            html_parts.append('<td class="null">null</td>')
            html_parts.append('<td class="null">null</td>')

        html_parts.append('</tr>')

    html_parts.append('</tbody>')
    html_parts.append('</table>')
    html_parts.append('</body>')
    html_parts.append('</html>')

    return '\n'.join(html_parts)


def main():
    """CLI entry point for dive/load comparison.

    Usage: python -m flightloads.aircraft_design_compare SPEC --output NEW_DIRECTORY

    Arguments:
        SPEC: Input spec JSON file path
        --output NEW_DIRECTORY: Output directory (created, must not exist)
    """
    import argparse

    parser = argparse.ArgumentParser(
        description='Compare multiple aircraft designs across screening cases'
    )
    parser.add_argument('spec_file', help='Input spec JSON file')
    parser.add_argument('--output', required=True, dest='output_dir',
                        help='Output directory (created, must not exist)')

    args = parser.parse_args()

    # Check output directory does not exist
    if os.path.exists(args.output_dir):
        print(f'Error: Output directory "{args.output_dir}" already exists', file=sys.stderr)
        sys.exit(1)

    # Read and parse spec
    try:
        with open(args.spec_file, 'r') as f:
            spec = json.load(f)
    except json.JSONDecodeError as e:
        print(f'Error: Invalid JSON in spec file: {e}', file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f'Error: Spec file not found: {args.spec_file}', file=sys.stderr)
        sys.exit(1)

    # Validate and run comparison
    try:
        report = compare_designs(spec)
    except ValueError as e:
        print(f'Error: Invalid input spec: {e}', file=sys.stderr)
        sys.exit(1)

    # Create output directory
    os.makedirs(args.output_dir)

    # Write JSON report
    json_path = os.path.join(args.output_dir, 'report.json')
    with open(json_path, 'w') as f:
        json.dump(report, f, indent=2, allow_nan=False)

    # Write HTML report
    html_path = os.path.join(args.output_dir, 'report.html')
    with open(html_path, 'w') as f:
        f.write(generate_html_report(report))

    print(f'Report written to {args.output_dir}/')
    print(f'  - report.json')
    print(f'  - report.html')


if __name__ == '__main__':
    main()
