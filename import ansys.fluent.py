import os
import sys

# --- FORCED BYPASS ---
# PyFluent is looking for 24.2, but you have 24.1. 
# We point the 24.2 variable to the 24.1 folder.
if not os.environ.get("AWP_ROOT242"):
    os.environ["AWP_ROOT242"] = "C:\\Program Files\\ANSYS Inc\\v241"

import ansys.fluent.core as pyfluent
import psutil
import matplotlib
matplotlib.use("Agg")  
import matplotlib.pyplot as plt
import re
import time

# --- CONFIGURATION ---
GEOMETRY_FILE = "FFF-3.dsco"

def force_pcores():
    """Finds Fluent processes and pins them to P-Cores (4-19)."""
    time.sleep(5) 
    p_core_ids = list(range(4, 20))
    targets = ["fl_mpi", "fluent", "ansys"]
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            name = proc.info["name"].lower()
            if any(t in name for t in targets):
                p = psutil.Process(proc.info["pid"])
                p.cpu_affinity(p_core_ids)
                p.nice(psutil.HIGH_PRIORITY_CLASS)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

def ti(s, cmd):
    out = s.scheme.exec((f'(ti-menu-load-string "{cmd}")',))
    return out if isinstance(out, str) else "\n".join(map(str, out))

def zone_id_by_name(s, zone_name: str) -> int:
    txt = ti(s, "/mesh/modify-zones/list")
    pattern = rf"^\s*(\d+)\s+{re.escape(zone_name)}\s+"
    m = re.search(pattern, txt, re.MULTILINE)
    if not m:
        raise ValueError(f"Zone name not found: '{zone_name}'")
    return int(m.group(1))

def cell_count_by_id(s, zid: int) -> int:
    txt = ti(s, "/mesh/mesh-info")
    m = re.search(rf"\b(\d+)\s+\w+\s+cells,\s+zone\s+{zid}\b", txt, re.I)
    return int(m.group(1)) if m else 0

def mesh_solver(run_id, vol_min, vol_max, surf_min, surf_max, fh): 
    print(f"Launching Fluent Meshing (Using v241 via AWP_ROOT242 bypass)...")
    # We pass 24.2 to satisfy the library requirements
    meshing_session = pyfluent.launch_fluent(
        precision="double", 
        processor_count=8, 
        mode="meshing",
        product_version="24.2" 
    )
    
    force_pcores() 
    watertight = meshing_session.watertight()
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    geo_path = os.path.join(script_dir, GEOMETRY_FILE)

    if not os.path.exists(geo_path):
        meshing_session.exit()
        raise FileNotFoundError(f"Missing geometry file: {geo_path}")

    # --- Meshing Workflow ---
    print("Importing Geometry...")
    watertight.import_geometry.file_name.set_state(geo_path)
    watertight.import_geometry()

    watertight.add_local_sizing.add_child_to_task()
    watertight.add_local_sizing()

    # ... after watertight.add_local_sizing() ...

    print(f"Setting Faceting and Surface Mesh: Min={surface_min_cell_length}, Max={surface_max_cell_length}")
    
    # 1. Sync CAD faceting with mesh sizes
    watertight.import_geometry.control_items["global_sizing"].faceting_max_size = surface_max_cell_length
    watertight.import_geometry.control_items["global_sizing"].faceting_min_size = surface_min_cell_length

    # 2. Set CFD Surface Mesh controls
    watertight.create_surface_mesh.cfd_surface_mesh_controls.min_size = surface_min_cell_length
    watertight.create_surface_mesh.cfd_surface_mesh_controls.max_size = surface_max_cell_length
    
    # 3. Generate
    watertight.create_surface_mesh()

    print("Generating Surface Mesh...")
    watertight.create_surface_mesh.cfd_surface_mesh_controls.min_size = surf_min
    watertight.create_surface_mesh.cfd_surface_mesh_controls.max_size = surf_max
    watertight.create_surface_mesh()

    watertight.describe_geometry.setup_type.set_state("The geometry consists of only fluid regions with no voids")
    watertight.describe_geometry()

    watertight.apply_share_topology()
    watertight.update_boundaries()
    watertight.update_regions()
    
    print("Adding Boundary Layers...")
    watertight.add_boundary_layer.first_height.set_state(fh)
    watertight.add_boundary_layer.insert_compound_child_task()

    print("Generating Volume Mesh...")
    create_vol_mesh = watertight.create_volume_mesh
    create_vol_mesh.volume_fill.set_state("poly-hexcore")
    create_vol_mesh.volume_fill_controls.hex_min_cell_length = vol_min
    create_vol_mesh.region_hex_name_list.set_state(["vessel_end", "main_vessel", "vessel_start"])
    create_vol_mesh.region_hex_max_cell_length_list.set_state([0.0048, vol_max, 0.0048])
    create_vol_mesh()
    
    solver_session = meshing_session.switch_to_solver()
    zid = zone_id_by_name(solver_session, "main_vessel")
    count = cell_count_by_id(solver_session, zid)

    print(f"Run {run_id} complete. Cell count: {count}")
    solver_session.exit()
    return count

if __name__ == "__main__":
    s_min, s_max = 7.62e-06, 6.35e-05
    v_min, v_max = 1.31e-05, 1.09e-04
    f_h = 2.41e-06
    r_factor = 1.2
    
    results = []

    for run_id in range(1, 5):
        print(f"\n--- STARTING RUN {run_id} ---")
        try:
            cell_count = mesh_solver(run_id, v_min, v_max, s_min, s_max, f_h)
            results.append({"id": run_id, "cells": cell_count})
        except Exception as e:
            print(f"Run {run_id} failed: {e}")
            break

        s_min /= r_factor
        s_max /= r_factor
        v_min /= r_factor
        v_max /= r_factor
        f_h /= r_factor

    if results:
        ids = [r['id'] for r in results]
        counts = [r['cells'] for r in results]
        plt.figure(figsize=(10, 6))
        plt.plot(ids, counts, marker='o', color='red')
        plt.xlabel("Iteration")
        plt.ylabel("Cell Count")
        plt.title(f"Mesh Convergence Study")
        plt.grid(True)
        os.makedirs("plots", exist_ok=True)
        plt.savefig("plots/mesh_convergence.png")