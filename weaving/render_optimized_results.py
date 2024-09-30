elastic_rods_dir = '../elastic_rods/python/'
weaving_dir = './'
import os
import os.path as osp
import sys; sys.path.append(elastic_rods_dir); sys.path.append(weaving_dir)
import numpy as np, elastic_rods, linkage_vis
import numpy.linalg as la
from bending_validation import suppress_stdout as so
import matplotlib.pyplot as plt
from elastic_rods import EnergyType, InterleavingType

import analysis_helper, ribbon_linkage_helper, mesh_vis, linkage_utils, compute_curve_from_curvature, pipeline_helper, optimization_visualization_helper, structural_analysis, importlib
importlib.reload(analysis_helper)
importlib.reload(ribbon_linkage_helper)
importlib.reload(mesh_vis)
importlib.reload(linkage_utils)
importlib.reload(compute_curve_from_curvature)
importlib.reload(pipeline_helper)
importlib.reload(optimization_visualization_helper)
importlib.reload(structural_analysis)

from analysis_helper import (compare_turning_angle,
                            is_on_sphere, 
                            get_distance_to_center_scalar_field, 
                            plot_curvatures, 
                            get_curvature_scalar_field,
                            construct_elastic_rod_loop_from_rod_segments, 
                            concatenate_rod_properties_from_rod_segments, 
                            compute_min_distance_rigid_transformation)
from ribbon_linkage_helper import (update_rest_curvature, 
                                   set_ribbon_linkage,
                                   export_linkage_geometry_to_obj,
                                   write_linkage_ribbon_output_florin)

from compute_curve_from_curvature import (match_geo_curvature_and_edge_len, get_all_curve_pattern)
from linkage_utils import order_segments_by_ribbons, get_turning_angle_and_length_from_ordered_rods

from pipeline_helper import (initialize_linkage, get_normal_deviation, set_joint_vector_field, stage_1_optimization, initialize_stage_2_optimizer, stage_2_optimization, InputOrganizer, write_all_output, set_surface_view_options, get_structure_analysis_view, contact_optimization, get_double_side_view, show_selected_joints, highlight_rod_and_joint, get_max_distance_to_target_surface, get_average_distance_to_target_joint, get_fixed_boundary_joint)

from optimization_visualization_helper import (compute_visualization_data_from_raw_data, get_objective_components_stage1, get_objective_components_stage2, get_objective_components_stage3, set_figure_label_and_limit, Visualization_Setting, plot_objective, plot_objective_stack, plot_ribbon_component_analysis, insert_nan, get_grad_norm_components_stage2, get_grad_norm_components_stage3)


from ribbon_linkage_helper import (update_rest_curvature, 
                                   set_ribbon_linkage,
                                   export_linkage_geometry_to_obj,
                                   write_linkage_ribbon_output_florin,
                                   write_distance_to_linkage_mesh)

from compute_curve_from_curvature import (match_geo_curvature_and_edge_len, get_all_curve_pattern)
from linkage_utils import order_segments_by_ribbons, get_turning_angle_and_length_from_ordered_rods

from pipeline_helper import (initialize_linkage, get_normal_deviation, set_joint_vector_field, stage_1_optimization, initialize_stage_2_optimizer, stage_2_optimization, InputOrganizer, write_all_output, set_surface_view_options, get_structure_analysis_view, get_max_distance_to_target_surface, contact_optimization, show_selected_joints, highlight_rod_and_joint)

import vis.fields
import matplotlib.cm as cm
import time

import matplotlib.pyplot as plt
import json
import pickle
import gzip

# Parallelism settings.
import parallelism
parallelism.set_max_num_tbb_threads(24)
parallelism.set_hessian_assembly_num_threads(8)
parallelism.set_gradient_assembly_num_threads(8)

import multiprocessing as mp
import py_newton_optimizer

# Optimization parameters.
OPTS = py_newton_optimizer.NewtonOptimizerOptions()
OPTS.gradTol = 1e-8
OPTS.verbose = 1;
OPTS.beta = 1e-8
OPTS.niter = 200
OPTS.verboseNonPosDef = False
rw = 0.1
sw = 10
drw = 0.01
dsw = 0.1

# If DEBUG set to true, only compute four data point for visualization for each stage.
DEBUG = False
# If only_two_stage, then the contact optimization is not used. 
only_two_stage = False

def make_patch_spines_invisible(ax):
    ax.set_frame_on(True)
    ax.patch.set_visible(False)
    for sp in ax.spines.values():
        sp.set_visible(False)

vs = Visualization_Setting()

with open(osp.join(weaving_dir + 'woven_model.json')) as f:
    data = json.load(f)

data_root = 'weave_from_topology_result'
compute_visualization_data = False
use_svg = False
def get_optimization_diagram_worker(model_info):
    ''' Run multi-stage optimization given the information about the model. Then for each iteration, compute data about the model and the ribbons. Lastly, generate plots for the objective functions and the descriptive data. 
    '''
    # Load model info from the json data.
    thickness, width, name, use_constant_width, width_scale, scale_joint_weight, update_attraction_weight, number_of_updates, fix_boundary, only_two_stage = model_info['thickness'], model_info['width'], model_info['name'], model_info['constant_cross_section'], model_info['cross_section_scale'], model_info['scale_joint_weight'], model_info['update_attraction_weight'], model_info['number_of_updates'], model_info['fix_boundary'], model_info['only_two_stage']
    
    print('Processing ', name)
    # Load feature joint weight if there is any.
    joint_weight, scale, joint_list = 0, 0, []
    if float(scale_joint_weight.split(', ')[0]) != -1:
        joint_weight, scale, joint_list = float(scale_joint_weight.split(', ')[0]), float(scale_joint_weight.split(', ')[1]), [int(x) for x in scale_joint_weight.split(', ')[2:]]
    
    # Set the interleaving type with special consideration for the bunny.
    interleaving_type = InterleavingType.triaxialWeave
    if name in ['bunny_head_small_triaxial_1', 'owl_1', 'clam_1']:
        interleaving_type = InterleavingType.weaving
    io = InputOrganizer(name, thickness, width, weaving_dir)

    # Create folders for storing computed data.
    if not os.path.exists('{}/{}'.format(data_root, name)):
        os.makedirs('{}/{}'.format(data_root, name))  

    if not os.path.exists('{}/{}/pickle'.format(data_root, name)):
        os.makedirs('{}/{}/pickle'.format(data_root, name)) 
    from PIL import Image
    def convert_png_to_jpg(image_name):
        new_image_name = image_name[:-4] + '.jpg'
        if not os.path.isfile(new_image_name):
            im = Image.open(image_name)
            bg = Image.new('RGB', im.size, (255,255,255))
            bg.paste(im,im)
            bg.save(new_image_name)
    if os.path.exists('{}/optimized_{}.png'.format(data_root, name)):
        convert_png_to_jpg('{}/optimized_{}.png'.format(data_root, name))
        return
    # Define data file names. 
    data_filename = '{}/{}_{}_data.npy'.format('{}/{}'.format(data_root, name), name, 'full' if not DEBUG else 'finite_sample')
    stage_1_data_filename = '{}/{}_stage_1.npy'.format('{}/{}'.format(data_root, name), name)
    stage_1_pickle_filename = '{}/{}_stage_1.pkl.gz'.format('{}/{}'.format(data_root, name), name)
    stage_2_data_filename = '{}/{}_stage_2.npy'.format('{}/{}'.format(data_root, name), name)
    stage_2_pickle_filename = '{}/{}_stage_2.pkl.gz'.format('{}/{}'.format(data_root, name), name)
    stage_2_weight_change_filename = '{}/{}_stage_2_weight_change.npy'.format('{}/{}'.format(data_root, name), name)
    stage_2_target_weight_filename = '{}/{}_stage_2_target_weight.npy'.format('{}/{}'.format(data_root, name), name)
    stage_3_data_filename = '{}/{}_stage_3.npy'.format('{}/{}'.format(data_root, name), name)
    stage_3_pickle_filename = '{}/{}_stage_3.pkl.gz'.format('{}/{}'.format(data_root, name), name)
    stage_3_weight_filename = '{}/{}_contact_weight_stage_3.npy'.format('{}/{}'.format(data_root, name), name)

    stage_1_vis_data_filename = '{}/{}_stage_1_vis.npy'.format('{}/{}'.format(data_root, name), name)
    stage_2_vis_data_filename = '{}/{}_stage_2_vis.npy'.format('{}/{}'.format(data_root, name), name)
    stage_3_vis_data_filename = '{}/{}_stage_3_vis.npy'.format('{}/{}'.format(data_root, name), name)

    stage_1_dof_filename = '{}/{}_stage_1_dof.npy'.format('{}/{}'.format(data_root, name), name)
    stage_2_dof_filename = '{}/{}_stage_2_dof.npy'.format('{}/{}'.format(data_root, name), name)
    stage_3_dof_filename = '{}/{}_stage_3_dof.npy'.format('{}/{}'.format(data_root, name), name)

    stage_2_solverStatus_filename = '{}/{}_stage_2_solverStatus.npy'.format('{}/{}'.format(data_root, name), name)
    stage_3_solverStatus_filename = '{}/{}_stage_3_solverStatus.npy'.format('{}/{}'.format(data_root, name), name)

    init_time_filename = '{}/{}_init_time.npy'.format('{}/{}'.format(data_root, name), name)

    print('Optimizing ', name)
    # Initialize the linkage class.
    start_time = time.time()
    if only_two_stage:
        curved_linkage = pickle.load(gzip.open('mega_monster_optimization_diagram_results/tenth_round/{}/{}_stage_2.pkl.gz'.format(name, name), 'r'))
    else:
        curved_linkage = pickle.load(gzip.open('mega_monster_optimization_diagram_results/tenth_round/{}/{}_stage_3.pkl.gz'.format(name, name), 'r'))

    print( io.SURFACE_PATH, io.MODEL_PATH,  io.RIBBON_CS,  io.SUBDIVISION_RESOLUTION, interleaving_type,  use_constant_width,  width_scale)

    # Set design parameter to include both rest length and rest curvature.
    # For some model the boundary joint positions need to be fixed. 
    fixed_boundary_joints = []
    if fix_boundary:
        fixed_boundary_joints = get_fixed_boundary_joint(curved_linkage)
    # Scale the attraction weights for feature joints if any. 
    if float(scale_joint_weight.split(', ')[0]) != -1:
        curved_linkage.scaleJointWeights(joint_weight, scale, joint_list)

    curved_linkage.attraction_weight = 1e-5
    optimizer = None
    after_initialization_time = time.time()

    with so(): elastic_rods.compute_equilibrium(curved_linkage, callback = None, options = OPTS, fixedVars = fixed_boundary_joints)
    curved_linkage_view = linkage_vis.LinkageViewer(curved_linkage)
    renderCam = curved_linkage_view.getCameraParams()
    # np.save('{}_renderCam.npy'.format(name), renderCam)

    def renderToFile(view, renderCam, path):
        orender = view.offscreenRenderer(width=2048, height=2048)
        orender.setCameraParams(renderCam)
        orender.render()
        orender.save(path)


    distance_color = write_distance_to_linkage_mesh(curved_linkage, max(io.RIBBON_CS), None, return_distance_field = True)
    curved_linkage_view.update(scalarField = distance_color[:, :3])
    renderToFile(curved_linkage_view, renderCam, '{}/optimized_{}.png'.format(data_root, name))

# get_optimization_diagram_worker(data['models'][8])
# print(len(data['models']))
# get_optimization_diagram_worker(data['models'][2])
# NUM_CORE = 4
# pool = mp.Pool(NUM_CORE)
# pool.map(get_optimization_diagram_worker, (model_info for model_info in data['models'][1:]))


# for index, model_info in enumerate(data['models']):
#     if index not in [0, 1, 2, 3, 4, 5, 11, 12, 13]:
#         get_optimization_diagram_worker(model_info)

for index, model_info in enumerate(data['models']):
    get_optimization_diagram_worker(model_info)
