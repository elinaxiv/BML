import os
import sys

# --- FORCED BYPASS FOR v241 ---
# PyFluent (0.24.x) looks for 'AWP_ROOT242' by default. 
# We manually point that variable to your actual v241 installation folder.
if not os.environ.get("AWP_ROOT242"):
    os.environ["AWP_ROOT242"] = "C:\\Program Files\\ANSYS Inc\\v241"import ansys.fluent.core as pyfluent
import psutil
import matplotlib
matplotlib.use("Agg")  # no Tkinter, no windows, safe for headless automation
import matplotlib.pyplot as plt
import re
import os

def force_pcores(): #Having some issues setting priority
    # P-cores on 12700KF = logical processors 4–19
    mask = 0
    for cpu in range(4, 20):
        mask |= 1 << cpu
    
    for proc in psutil.process_iter(["name", "pid"]):
        if "fl_mpi" in proc.info["name"].lower() or "fluent" in proc.info["name"].lower():
            try:
                p = psutil.Process(proc.info["pid"])
                p.cpu_affinity(list(range(4, 20)))  # set to P-cores 4-19
                p.nice(psutil.HIGH_PRIORITY_CLASS)
                # print(f"Set P-cores + high priority: {proc.info['name']} (PID {proc.info['pid']})")
            except:
                pass


def ti(s, cmd):
    out = s.scheme.exec((f'(ti-menu-load-string "{cmd}")',))
    return out if isinstance(out, str) else "\n".join(map(str, out))


def cell_count_by_zone_id(s, zid: int) -> int:
    txt = ti(s, "/mesh/mesh-info")
    m = re.search(rf"\b(\d+)\s+\w+\s+cells,\s+zone\s+{zid}\b", txt, re.I)
    if not m:
        raise ValueError(f"No cell line found for zone {zid}")
    return int(m.group(1))

def zone_id_by_name_from_modify_list(s, zone_name: str) -> int:
    """
    Reads /mesh/modify-zones/list and finds the numeric zone id for a given name.
    Matches exact name.
    """
    txt = ti(s, "/mesh/modify-zones/list")

    # lines look like:
    #  486  model-for-phill_sw2023-more-fullydev:1  fluid  air  cell
    pattern = rf"^\s*(\d+)\s+{re.escape(zone_name)}\s+"
    m = re.search(pattern, txt, re.MULTILINE)
    if not m:
        raise ValueError(f"Zone name not found in modify-zones/list: '{zone_name}'")
    return int(m.group(1))


def cell_count_by_zone_name(s, zone_name: str) -> int:
    zid = zone_id_by_name_from_modify_list(s, zone_name)
    return cell_count_by_zone_id(s, zid)


def build_mesh_family(hmin0, hmax0, rL, nlevels):
    """Prescribed mesh family using h_{k+1} = h_k / rL."""
    hs = []
    hmin = float(hmin0)
    hmax = float(hmax0)
    for k in range(nlevels):
        hs.append({"level": k+1, "h_min": hmin, "h_max": hmax})
        hmin = hmin / rL
        hmax = hmax / rL
    return hs

def r_eff_from_cells(Nk, Nk1):
    """Effective refinement ratio from cell counts: r_eff = (Nk1/Nk)^(1/3)."""
    Nk = float(Nk); Nk1 = float(Nk1)
    if Nk <= 0 or Nk1 <= 0:
        raise ValueError("Cell counts must be positive.")
    return (Nk1 / Nk) ** (1.0/3.0)

def mesh_solver(run_id, vol_min_cell_length, vol_max_cell_length, surface_min_cell_length, surface_max_cell_length, firstheight, refine_factor): 
    meshing_session = pyfluent.launch_fluent(precision="double", processor_count=8, mode="meshing")
    force_pcores() #This forces all cores to run at max priority 
    watertight = meshing_session.watertight()
    import_file_name = 'FFF-3.dsco'

    watertight.import_geometry.file_name.set_state(import_file_name)
    watertight.import_geometry.length_unit.set_state('m')
    watertight.import_geometry()

    watertight.add_local_sizing.add_child_to_task()
    watertight.add_local_sizing()

    watertight.create_surface_mesh.cfd_surface_mesh_controls.min_size = surface_min_cell_length
    watertight.create_surface_mesh.cfd_surface_mesh_controls.max_size = surface_max_cell_length
    watertight.create_surface_mesh()

    watertight.describe_geometry.update_child_tasks(setup_type_changed=False)
    watertight.describe_geometry.setup_type.set_state("The geometry consists of only fluid regions with no voids")
    watertight.describe_geometry.wall_to_internal.set_state("Yes")
    watertight.describe_geometry.update_child_tasks(setup_type_changed=True)
    watertight.describe_geometry()

    watertight.apply_share_topology()
    watertight.update_boundaries()
    watertight.update_regions()
    
    # target_total_thickness = 0.0015
    growth_rate = 1.1

    # import math
    # term1 = 1 - (target_total_thickness / current_first_height) * (1 - growth_rate)
    # if term1 <= 0: term1 = 1e-6 

    watertight.add_boundary_layer.bl_control_name.set_state("uniform_1")
    watertight.add_boundary_layer.offset_method_type.set_state("uniform")
    watertight.add_boundary_layer.first_height.set_state(firstheight)
    watertight.add_boundary_layer.number_of_layers.set_state(12)
    watertight.add_boundary_layer.rate.set_state(growth_rate)
    watertight.add_boundary_layer.insert_compound_child_task()


    create_vol_mesh = watertight.create_volume_mesh
    create_vol_mesh.volume_fill.set_state("poly-hexcore")
    create_vol_mesh.sizing_method.set_state("Region-based")
    create_vol_mesh.volume_fill_controls.peel_layers = 1
    create_vol_mesh.prism_preferences.prism_gap_factor = 0.45
    create_vol_mesh.prism_preferences.show_prism_preferences.set_state(True)
    create_vol_mesh.volume_mesh_preferences.show_volume_mesh_preferences.set_state(True)
    create_vol_mesh.volume_fill_controls.hex_min_cell_length = vol_min_cell_length # Parameterize this as well 5e-04
    create_vol_mesh.volume_mesh_preferences.quality_method = "Enhanced Orthogonal"
    create_vol_mesh.region_hex_name_list.set_state([
        "vessel_end",
        "main_vessel",
        "vessel_start"
    ])

    create_vol_mesh.region_hex_max_cell_length_list.set_state([ #Need to automate and paramterize region 2 (the middle one) 0.005
        0.0048, vol_max_cell_length, 0.0048
    ])
    create_vol_mesh()
    

    solver_session = meshing_session.switch_to_solver()
    solver_session.settings.mesh.check()

    ti(solver_session, "/mesh/modify-zones/list")
    cell_count = cell_count_by_zone_name(solver_session, "main_vessel")

    solver_session.exit()

    return cell_count

history = []
# surface_min_cell_length0 = 0.000025
# surface_max_cell_length0 = 0.000125
surface_min_cell_length0 = 7.62796E-06
surface_max_cell_length0 = 6.35664E-05
vol_min_cell_length0 = 1.3181114969135804e-05
vol_max_cell_length0 = 1.0984278549382718E-04
firstheight0 = 2.411265432098766e-06
refine_factor = 1.2  # prescribed length refinement ratio rL
refine_factor_list = [1.2]  # <-- for later use if we want to test different rL values

for x in refine_factor_list:
    refine_factor = x
    history = []
    surface_min_cell_length = surface_min_cell_length0
    surface_max_cell_length = surface_max_cell_length0
    vol_min_cell_length = vol_min_cell_length0
    vol_max_cell_length = vol_max_cell_length0
    firstheight = firstheight0
    for j in range(1,5):
        run_id = j


        print(f"Starting run {run_id} with vol_min_cell_length={vol_min_cell_length} and vol_max_cell_length={vol_max_cell_length}")
        current_out = mesh_solver(run_id, vol_min_cell_length, vol_max_cell_length, surface_min_cell_length, surface_max_cell_length, firstheight, refine_factor)

        history.append({
            "run_id": run_id,
            "cell_count": current_out,
            "surf_min" : surface_min_cell_length,
            "surf_max": surface_max_cell_length,
            "vol_min": vol_min_cell_length,
            "vol_max": vol_max_cell_length,
            "height": firstheight
        })

        # Step A: prescribed sizing (same as before; refine_factor acts as rL)
        surface_max_cell_length = surface_max_cell_length / refine_factor
        surface_min_cell_length = surface_min_cell_length / refine_factor
        vol_max_cell_length = vol_max_cell_length / refine_factor
        vol_min_cell_length = vol_min_cell_length / refine_factor
        firstheight = firstheight / refine_factor
    h = [entry['run_id'] for entry in history]
    Q = [entry['cell_count'] for entry in history]

    for entry in history:
        print(f"Run {entry['run_id']}: Cell Count = {entry['cell_count']}")
        print(f"Surface min cell length: {entry['surf_min']} Surface max cell length: {entry['surf_max']}")
        print(f"Volume min cell length: {entry['vol_min']} Volume max cell length: {entry['vol_max']}")
        print(f"Firstheight: {entry['height']}")
        

    plt.figure()
    plt.plot(h, Q, marker='o')
    plt.xlabel("Run ID")
    plt.ylabel("Cell Count")
    plt.title(f"Cell Count vs Run ID for refinement factor {refine_factor}")
    plt.grid(True)

    #Create folder if it doesn't exist
    os.makedirs("plots", exist_ok=True)
    filename = f"plots/cell_count_vs_run_r{refine_factor}.png"            
    plt.savefig(filename, dpi=300, bbox_inches="tight")
    plt.close()

    print(f"Saved plot to {filename}")
