# This uses ConvHurt to convert the network created by Network_connect.py

#
# TODO:
# - Check what axon and dendrite propagation speeds should be
#

import json
import os
import sys
from collections import OrderedDict

import h5py
import numpy as np

from snudda.utils import snudda_parse_path
from snudda.utils.conv_hurt import ConvHurt
from snudda import SnuddaLoad


class ExportSonata(object):

    def __init__(self, network_file, input_file, out_dir, debug_flag=True):

        self.debug = debug_flag

        self.out_dir = out_dir

        # Read the data
        self.snudda_load = SnuddaLoad(network_file)
        self.network_file = self.snudda_load.network_file
        self.input_file = input_file

        self.network_config = json.loads(self.snudda_load.data["config"])

        print(f"Using input file: {self.input_file}")

        # This contains data for converting to Neurodamus secID and secX
        self.morph_cache = dict([])

        self.morph_location_lookup = dict([])
        self.hoc_location_lookup = dict([])

        # Write the data in new format using the ConvHurt module
        # TODO: We need to read structure names from the network-config.json
        structure_names = [x for x in self.network_config["Volume"]]
        ch = ConvHurt(simulation_structures=structure_names,
                      base_dir=out_dir)

        self.copy_morphologies()
        self.copy_mechanisms()
        self.copy_hoc()

        # Convert our rotation matrices to vx,vy,vz rotation angles
        # Neurodamus rotation order is first Z then Y and X.
        (vx, vy, vz) = self.rot_angles_zyx(self.snudda_load)

        # We need to convert the named neuron types to numbers
        (node_type_id, node_type_id_lookup) = self.allocate_node_type_id()
        (node_group_id, group_idx, group_lookup) = self.allocate_node_groups()

        volume_id_list = [x["volumeID"] for x in self.snudda_load.data["neurons"]]
        volume_list = set(volume_id_list)

        # node_name_list = [x["name"] for x in self.snudda_load.data["neurons"]]
        node_name_list = [f"{n['name']}_{n['morphologyKey']}_{n['parameterKey']}_{n['modulationKey']}"
                          for n in self.snudda_load.data["neurons"]]

        # Edge data is stored in a HDF5 file and a CSV file
        edge_group_lookup = self.allocate_edge_groups()

        edge_type_lookup = dict()
        for (pre_type, post_type), con_data in self.snudda_load.data["connectivityDistributions"].items():
            for con_type, con_type_data in con_data.items():
                if con_type == "GapJunction":
                    # TODO: How do we write gap junctions to SONATA?
                    continue
                else:
                    edge_type_id = con_type_data["channelModelID"]
                    edge_model = con_type_data["channelParameters"]["modFile"]

                edge_type_lookup[pre_type, post_type, con_type] = (edge_type_id, edge_model)

        for volume_name in volume_list:
            v_idx = np.where([v == volume_name for v in volume_id_list])[0]

            # Node data is stored in a HDF5 file and a CSV file (CONVERT TO micrometers)
            node_data = OrderedDict([("x", self.snudda_load.data["neuronPositions"][v_idx, 0] * 1e6),
                                     ("y", self.snudda_load.data["neuronPositions"][v_idx, 1] * 1e6),
                                     ("z", self.snudda_load.data["neuronPositions"][v_idx, 2] * 1e6),
                                     ("rotation_angle_zaxis", vz[v_idx]),
                                     ("rotation_angle_yaxis", vy[v_idx]),
                                     ("rotation_angle_xaxis", vx[v_idx])])

            node_id_list = np.array([x["neuronID"] for x in self.snudda_load.data["neurons"]
                                     if x["volumeID"] == "Striatum"], dtype=int)

            assert (v_idx == node_id_list).all()

            ch.write_nodes(node_file=f"{volume_name}.hdf5",
                           population_name=volume_name,
                           data=node_data,
                           node_id=node_id_list,
                           node_type_id=node_type_id[node_id_list],
                           node_group_id=node_group_id[node_id_list],
                           node_group_index=group_idx[node_id_list])

            csv_node_names = sorted(list(set([node_name_list[x] for x in v_idx])))
            csv_node_type_id = [node_type_id_lookup[n] for n in csv_node_names]
            csv_node_location = [volume_name for n in csv_node_names]

            (model_template_list, model_type_list, morphology_list) = self.get_node_templates(csv_node_names)

            node_data_csv = OrderedDict([("model_type", model_type_list),
                                         ("model_template", model_template_list),
                                         ("location", csv_node_location),
                                         ("morphology", morphology_list),
                                         ("model_name", csv_node_names)])

            ch.write_node_csv(node_csv_file=f"{volume_name}_node_types.csv",
                              node_type_id=csv_node_type_id,
                              data=node_data_csv)

            (edge_group, edge_group_index, edge_type_id, source_gid, target_gid, edgeData) \
                = self.setup_edge_info(edge_group_lookup)

            ch.write_edges(edge_file=f"{volume_name}_edges.hdf5",
                           edge_population_name=volume_name,
                           edge_group=edge_group,
                           edge_group_index=edge_group_index,
                           edge_type_id=edge_type_id,
                           source_id=source_gid,
                           target_id=target_gid,
                           data=edgeData)

            edge_type_id = [x[1] for x in edge_type_lookup.values()]
            edge_data = {"template" : [x[0] for x in edge_type_lookup.values()]}

            ch.write_edges_csv(edge_csv_file=f"{volume_name}_edge_types.csv",
                               edge_type_id=edge_type_id,
                               data=edge_data)

        # !!! WE ALSO NEED TO ADD BACKGROUND INPUT TO THE NEURONS IN THE NETWORK

        self.write_simulation_config()

        print(f"SONATA files exported to {out_dir}")

    ############################################################################

    def get_edge_type_lookup(self):

        # Read the config file, to find out all possible types of connections

        pass

    ############################################################################

    def rot_mat_to_angles_xyz(self, rot_mat):
        # http://nghiaho.com/?page_id=846

        # This is for Rz*Ry*Rx
        vx = np.arctan2(rot_mat[2, 1], rot_mat[2, 2])
        vy = np.arctan2(-rot_mat[2, 0], np.sqrt(rot_mat[2, 1] ** 2 + rot_mat[2, 2] ** 2))
        vz = np.arctan2(rot_mat[1, 0], rot_mat[0, 0])

        # !! We need to do this for Rx*Ry*Rz
        assert False

        return vx, vy, vz

    ############################################################################

    def rot_mat_to_angles_zyx(self, rotMat):
        vz = np.arctan2(-rotMat[0, 1], rotMat[0, 0])
        vy = np.arcsin(rotMat[0, 2])
        vx = np.arctan2(-rotMat[1, 2], rotMat[2, 2])

        return vx, vy, vz

    ############################################################################

    def angles_to_rot_mat(self, v):
        (vx, vy, vz) = v
        RX = np.array([[1., 0., 0.],
                       [0., np.cos(vx), -np.sin(vx)],
                       [0., np.sin(vx), np.cos(vx)]])

        RY = np.array([[np.cos(vy), 0., np.sin(vy)],
                       [0., 1., 0.],
                       [-np.sin(vy), 0, np.cos(vy)]])

        RZ = np.array([[np.cos(vz), -np.sin(vz), 0.],
                       [np.sin(vz), np.cos(vz), 0.],
                       [0., 0., 1.]])

        return np.matmul(RX, np.matmul(RY, RZ))
        # return np.matmul(np.matmul(RZ,RY),RX)

    ############################################################################

    def rot_angles_zyx(self, nl):

        x = np.zeros(len(nl.data["neurons"]))
        y = np.zeros(len(nl.data["neurons"]))
        z = np.zeros(len(nl.data["neurons"]))

        for idx in range(0, len(nl.data["neurons"])):
            ang = self.rot_mat_to_angles_zyx(nl.data["neurons"][idx]["rotation"])

            # Just verify that we calculated the angles right and can get old
            # rotation matrix back
            assert (np.sum(np.abs(self.angles_to_rot_mat(ang) - nl.data["neurons"][idx]["rotation"])) < 1e-9)
            # Save angles
            x[idx] = ang[0]
            y[idx] = ang[1]
            z[idx] = ang[2]

        return x, y, z

    ############################################################################

    def allocate_node_type_id(self):

        # Since each neuron directory can have multiple morphology/parameter sets
        # we need to use the name + morphology_key + parameter_key + modulation_key

        node_type_id_lookup = OrderedDict([])
        next_node_type_id = 0

        node_names = [f"{n['name']}_{n['morphologyKey']}_{n['parameterKey']}_{n['modulationKey']}"
                      for n in self.snudda_load.data["neurons"]]

        for n in node_names:
            if n not in node_type_id_lookup:
                node_type_id_lookup[n] = next_node_type_id
                next_node_type_id += 1

        node_type_id = np.array([node_type_id_lookup[x] for x in node_names], dtype=int)

        return node_type_id, node_type_id_lookup

    ############################################################################

    # Comment from 2018, hope they support all now (guess we will find out):
    #  --> RT neuron only supports one group, so put everything in the same group

    # Snudda neuron type corresponds to SONATA NodeGroup
    # SONATA NodeType is {Snudda neuron name}_{morphology_key}_{parameter_key}_{modulation_key}

    def allocate_node_groups(self):

        node_group_id = np.zeros(len(self.snudda_load.data["neurons"]), dtype=int)
        group_idx = np.zeros(len(self.snudda_load.data["neurons"]), dtype=int)
        group_lookup = dict([])

        neuron_types = [n["type"] for n in self.snudda_load.data["neurons"]]

        next_group_idx = dict()
        next_group = 0

        for nt in neuron_types:
            if nt not in group_lookup:
                group_lookup[nt] = next_group
                next_group += 1
                next_group_idx[nt] = 0

        for idx, nt in enumerate(neuron_types):
            node_group_id[idx] = group_lookup[nt]
            group_idx[idx] = next_group_idx[nt]
            next_group_idx[nt] += 1

        return node_group_id, group_idx, group_lookup

    ############################################################################

    def get_node_templates(self, csv_node_name):

        template_dict = dict([])
        model_type_dict = dict([])
        morphology_dict = dict([])
        missing_list = []

        # First create a lookup
        for n in self.snudda_load.data["neurons"]:
            hoc_file = n["hoc"]
            name = f"{n['name']}_{n['morphologyKey']}_{n['parameterKey']}_{n['modulationKey']}"

            # It seems that RT neuron requires the filename without .swc at the end
            # Also, they prepend morphology path, but only allows one directory for all morphologies

            morph_file = os.path.splitext(os.path.basename(n["morphology"]))[0]

            if hoc_file in self.hoc_location_lookup:
                # We need to change from old hoc location to the SONATA hoc location
                hoc_str = f"hoc:{os.path.basename(self.hoc_location_lookup[hoc_file])}"
            elif n["virtualNeuron"]:
                hoc_str = "virtual"
            else:
                hoc_str = "NULL"
                if hoc_file not in missing_list:
                    # Only write error ones per file
                    print(f"Missing hoc template: {hoc_file}")
                    missing_list.append(hoc_file)

            if name not in morphology_dict:
                morphology_dict[name] = morph_file
            else:
                assert os.path.basename(morphology_dict[name]) == os.path.basename(morph_file), \
                    f"Morphology mismatch for {name}: {morphology_dict[name]} vs {morph_file}"

            if name not in template_dict:
                template_dict[name] = hoc_str
                model_type_dict[name] = "biophysical"
            else:
                assert template_dict[name] == hoc_str, \
                    f"All files named {name} do not share same hoc file: {template_dict[name]} and {hoc_str}"

        # Need to put them in the order in csvNodeName

        template_list = [template_dict[x] for x in csv_node_name]
        model_type_list = [model_type_dict[x] for x in csv_node_name]
        morph_list = [morphology_dict[x] for x in csv_node_name]

        # import pdb
        # pdb.set_trace()

        return template_list, model_type_list, morph_list

    ############################################################################

    def get_node_templates_for_all(self, nl):

        print("This should return all the types of neurons, eg. FNS_0,FSN_1, ..., MSD1_0, ...")
        print("This is fewer than the number of neurons. Check CSV file to make sure it is ok after")

        import pdb
        pdb.set_trace()

        template_list = []
        model_type_list = []
        missing_list = []

        for neuron in nl.data["neurons"]:
            hoc_file = neuron["hoc"]

            model_type_list.append("biophysical".encode())

            if hoc_file in self.hoc_location_lookup:
                hoc_str = f"hoc:{self.hoc_location_lookup[hoc_file]}"
                template_list.append(hoc_str.encode())
            else:
                template_list.append("".encode())
                if hoc_file not in missing_list:
                    # Only write error ones per file
                    print(f"Missing hoc template: {hoc_file}")
                    missing_list.append(hoc_file)

        return template_list, model_type_list

    ############################################################################

    # !!! A lot of HBP tools only support the first group, so put all in group 0
    # The old code makes separate groups

    def allocate_edge_groups_2019(self):

        # FS->MSD1, FS->MSD2 etc are example of EdgeGroups
        edge_group_lookup = OrderedDict([])

        unique_group_names = set([self.snudda_load.data["neurons"][idx]["name"].split("_")[0]
                                  for idx in range(0, len(self.snudda_load.data["neurons"]))])

        for g1 in unique_group_names:
            for g2 in unique_group_names:
                edge_group_lookup[g1, g2] = 0

        return edge_group_lookup

    ############################################################################

    # !!! This is old version, that makes separate groups for different sets
    # of synapses, but HBP tools only support first group currently, Jan 2019.
    # so made another version of allocateEdgeGroups (see above).
    #
    # Update: It is 2023, going to see if it works to use again.

    def allocate_edge_groups(self):

        # FS->MSD1, FS->MSD2 etc are example of EdgeGroups
        edge_group_lookup = OrderedDict([])

        unique_group_names = set([self.snudda_load.data["neurons"][idx]["name"].split("_")[0]
                                  for idx in range(0, len(self.snudda_load.data["neurons"]))])

        next_group = 0

        for g1 in unique_group_names:
            for g2 in unique_group_names:
                edge_group_lookup[g1, g2] = next_group
                next_group += 1

        return edge_group_lookup

    ############################################################################

    # This code sets up the info about edges

    def setup_edge_info(self, edge_group_lookup):

        n_synapses = self.snudda_load.data["synapses"].shape[0]
        edge_group = np.zeros(n_synapses, dtype=int)
        edge_group_index = np.zeros(n_synapses, dtype=int)
        edge_type_id = np.zeros(n_synapses, dtype=int)
        source_gid = np.zeros(n_synapses, dtype=int)
        target_gid = np.zeros(n_synapses, dtype=int)

        sec_id = np.zeros(n_synapses, dtype=int)
        sec_x = np.zeros(n_synapses)
        syn_weight = np.zeros(n_synapses)
        delay = np.zeros(n_synapses)

        edge_group_count = np.zeros(len(edge_group_lookup.keys()))

        # Check if these speeds are reasonable?
        axon_speed = 25.0  # 25m/s
        dend_speed = 1.0

        # columns in nl.data["synapses"]
        # 0: sourceCellID, 1: sourceComp, 2: destCellID, 3: destComp,
        # 4: locType, 5: synapseType, 6: somaDistDend 7:somaDistAxon
        # somaDist is an int, representing micrometers

        for i_syn, syn_row in enumerate(self.snudda_load.data["synapses"]):
            source_gid[i_syn] = syn_row[0]
            target_gid[i_syn] = syn_row[1]
            sec_id[i_syn] = syn_row[9]
            sec_x[i_syn] = syn_row[10] / 1000.0
            synapse_type = syn_row[5]

            pre_type = self.snudda_load.data["neurons"][source_gid[i_syn]]["type"]
            post_type = self.snudda_load.data["neurons"][target_gid[i_syn]]["type"]

            edge_group[i_syn] = edge_group_lookup[pre_type, post_type]
            edge_type_id[i_syn] = synapse_type

            # edge_type_id[i_syn] = edge_type_lookup[pre_type, post_type, synapse_type]

            edge_group_index[i_syn] = edge_group_count[edge_group[i_syn]]
            edge_group_count[edge_group[i_syn]] += 1

            dend_dist = syn_row[6] * 1e-6
            axon_dist = syn_row[7] * 1e-6
            delay[i_syn] = axon_dist / axon_speed + dend_dist / dend_speed
            syn_weight[i_syn] = 1.0  # !!! THIS NEEDS TO BE SET DEPENDING ON CONNECTION TYPE

        edge_data = OrderedDict([("sec_id", sec_id),
                                 ("sec_x", sec_x),
                                 ("syn_weight", syn_weight),
                                 ("delay", delay)])

        # Need to set edgeGroup, edgeGroupIndex, edgeType also

        return (edge_group, edge_group_index, edge_type_id,
                source_gid, target_gid, edge_data)

    ############################################################################

    # Convert synapse coordinates (in SWC reference frame) to SectionID
    # and section_X (fractional distance along dendritic section)

    def conv_synapse_coord_to_section(self, cell_gid, orig_synapse_coord, network_info):

        assert False, "Depricated function: convSynapseCoordToSection"

        if self.debug:
            print("Converting coordinate to sections")

        cell_id = network_info.data["neurons"][cell_gid]["name"]

        # Get swc filename from cellID
        swc_file = network_info.config[cell_id]["morphology"]

        # Check if cell morphology is loaded in neuron cache
        # Columns in morph: 0:swcID, 1:x, 2:y, 3:z, 4:secID, 5:secX, 6:type
        if swc_file in self.morph_cache:
            morph = self.morph_cache[swc_file]
        else:
            morph = self.load_morph(swc_file)
            self.morph_cache[swc_file] = morph

            # VERIFICATION
            if False:
                self.plot_section_data(morph, swc_file)
                import pdb
                pdb.set_trace()

        # Find points matching the coords
        xyz = morph[:, 1:4]
        d = np.sum((xyz - orig_synapse_coord) ** 2, axis=-1)

        closest_idx = np.argsort(d)[0]
        sec_id = int(morph[closest_idx, 4])
        sec_x = morph[closest_idx, 5]

        # Check that compartment that matched is soma or dendrite
        try:
            assert morph[closest_idx, 6] in [1, 3, 4], \
                "Synapse location should be soma or dendrite"
        except:
            import pdb
            pdb.set_trace()

        if self.debug:
            print("match dmin=" + str(d[closest_idx] ** 0.5))

        # import pdb
        # pdb.set_trace()

        return sec_id, sec_x

    ############################################################################

    def convert_coordinates(self, morphology):

        # Loop through entire SWC morphology, calculate SEC_ID, SEC_X
        # for each vertex.

        print("TEST TEST TEST")
        import pdb
        pdb.set_trace()

    ############################################################################

    # This code builds the secID and secX from the SWC file

    def load_morph(self, swc_file, skip_axon=True):

        with open(swc_file, 'r') as f:
            lines = f.readlines()

        comp_type = {1: "soma", 2: "axon", 3: "dend", 4: "apic"}

        swc_vals = np.zeros(shape=(len(lines), 7))

        n_comps = 0
        for ss in lines:
            if ss[0] != '#':
                swc_vals[n_comps, :] = [float(s) for s in ss.split()]
                n_comps = n_comps + 1

        # Subtract 1 from ID and parentID, so we get easier indexing
        swc_vals[:, 0] -= 1
        swc_vals[:, 6] -= 1

        swc_vals[:, 2:5] *= 1e-6  # Convert to meter

        # Columns in vertex matrix:
        # 0:ID, 1:parentID, 2:x,3:y,4:z, 5:somaDist, 6:type, 7:childCount,
        # 8:nodeParent, 9:segLen, 10:seg_ID, 11:seg_X
        #
        # If childCount != 1 then vertex is a segment node
        vertex = np.zeros(shape=(n_comps, 12))

        for i in range(0, n_comps):
            # Check that the IDs are in order, and no missing values
            assert (swc_vals[i, 0] == i)

            # Check that parent already defined
            assert (swc_vals[i, 6] < swc_vals[i, 0])

            vertex[i, 0] = swc_vals[i, 0]  # swcID
            vertex[i, 2:5] = swc_vals[i, 2:5]  # x,y,z
            vertex[i, 6] = swc_vals[i, 1]  # type

            if swc_vals[i, 1] == 1:
                assert (i == 0)  # Soma should be first compartment

                vertex[i, 1] = -1  # parent_swcID
                vertex[i, 5] = 0  # soma dist
                vertex[i, 7] = 100  # Set child count > 1 for soma
                # to make sure it is a node

            elif swc_vals[i, 1] in [2, 3, 4]:
                # axon or dend
                parent_id = int(swc_vals[i, 6])  # parentID
                vertex[i, 1] = parent_id
                parent_coords = vertex[parent_id, 2:5]
                parent_dist = vertex[parent_id, 5]
                vertex[i, 5] = parent_dist + sum((vertex[i, 2:5] - parent_coords) ** 2) ** 0.5  # Soma dist

                # if(parentID == 1):
                #  # Soma as parent, set artificially high child count for compartment
                #  # so it is considered a node edge
                #  vertex[i,7] = 100
                # else:
                vertex[parent_id, 7] += 1  # Child count, nodes have != 1, i.e. 0 or 2,3,..

        if skip_axon:
            # Find all branch points and end points (excluding axons)
            node_idx = np.where(np.logical_and(vertex[:, 7] != 1, vertex[:, 1] != 2))[0]
        else:
            # Find all branch points and end points
            node_idx = np.where(vertex[:, 7] != 1)[0]

        # Find out how branch points are connected (set node parent)
        # OBS, parent is previous point in SWC file a point is connected to
        # For segments, node and node parent are the two end points
        for n_idx in node_idx:
            parent_id = int(vertex[n_idx, 1])
            vertex[n_idx, 8] = parent_id

            while vertex[parent_id, 7] == 1:
                # While the parent node has 1 child, and it is not soma
                parent_id = int(vertex[parent_id, 1])

                if vertex[parent_id, 6] == 1:
                    # This is a soma, dont use it as parent
                    break

                assert (n_idx == 0 or parent_id >= 0)
                vertex[n_idx, 8] = parent_id  # node parent

        # Number the segments, start with soma which has ID 0
        assert ((np.diff(node_idx) > 0).all())  # Check they are in right order

        for sec_id, n_idx in enumerate(node_idx):
            vertex[n_idx, 10] = sec_id

        # For each connected pair of branch points (dont forget soma)
        # calculate the total length of the segment (also note if soma,axon or dend)

        for n_idx in node_idx:

            if n_idx == 0:
                vertex[n_idx, 9] = 1  # segLen (to avoid divide by zero for soma)
            else:
                # Difference in soma dist between node and node parent
                parent_id = int(vertex[n_idx, 8])
                vertex[n_idx, 9] = vertex[n_idx, 5] - vertex[parent_id, 5]  # segLen

        # For each vertex in a segment, calculate the seg_X (value 0 to 1)
        # Save seg_ID and seg_X for each vertex, its coordinates, and S/A/D type

        for n_idx in node_idx:

            if n_idx == 0:
                vertex[n_idx, 11] = 0.5
            # elif(vertex[nIdx,1] == 0):
            #  # Soma is the parent, special case (Neuron has no compartment
            #  # linking soma centre to first axon/dendirte node)
            #  vertex[nIdx,11] = 0
            else:
                vertex[n_idx, 11] = 1  # secX
                end_soma_dist = vertex[n_idx, 5]
                seg_len = vertex[n_idx, 9]

                parent_id = int(vertex[n_idx, 1])

                while vertex[parent_id, 7] == 1:  # While the parent node has 1 child
                    # secID
                    vertex[parent_id, 10] = vertex[n_idx, 10]

                    # secX
                    vertex[parent_id, 11] = 1 - (end_soma_dist - vertex[parent_id, 5]) / seg_len
                    parent_id = int(vertex[parent_id, 1])

                    assert (parent_id >= 0)

        # swcID, x,y,z coord, secID, secX, type
        if skip_axon:
            non_axon_idx = np.where(vertex[:, 6] != 2)[0]
            return vertex[:, [0, 2, 3, 4, 10, 11, 6]][non_axon_idx, :]
        else:
            return vertex[:, [0, 2, 3, 4, 10, 11, 6]]

    ############################################################################

    # !!! PLOT TO VERIFY

    def plot_section_data(self, morph, swc_file):
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d import Axes3D

        print("Plotting verification of sections")

        from snudda.neurons.neuron_morphology import NeuronMorphology
        n = NeuronMorphology(swc_filename=swc_file,
                             verbose=True)
        ax = n.plot_neuron(plot_axon=False)

        for row in morph:
            # Plot secID and secX
            # import pdb
            # pdb.set_trace()
            s = str(row[4]) + "-" + str(round(row[5], 2))
            txt = ax.text(x=row[1], y=row[2], z=row[3], s=s, color="blue")
            print(s + "Press key")
            input('')
            # txt.remove()

        plt.ion()
        plt.show()
        plt.draw()
        plt.pause(0.001)

    ############################################################################

    def write_simulation_config(self):

        sim_conf = dict([])

        sim_conf["manifest"] = {
            "$BASE_DIR": ".",
            "$NETWORK_DIR": "$BASE_DIR/networks",
            "$COMPONENT_DIR": "$BASE_DIR/components",
            "$INPUT_DIR": "$BASE_DIR/inputs",
            "$OUTPUT_DIR": "$BASE_DIR/output"}

        sim_conf["run"] = {
            "tstop": 1000.0,
            "dt": 0.1,
            "dL": 15,
            "spike_threshold": -15}

        sim_conf["conditions"] = {}

        sim_conf["output"] = {"log_file": "$OUTPUT_DIR/striatum-log.txt",
                              "output_dir": "$OUTPUT_DIR"}

        sim_conf["overwrite_output_dir"] = True

        sim_conf["network"] = "$BASE_DIR/circuit_config.json"

        # node_sets_file contains the reports we want written from the simulation
        # simConf["node_sets_file"] = None # !!! THIS NEEDS TO BE WRITTEN

        cortex_input = {"input_type": "spikes",
                        "module": "h5",
                        "input_file": "$INPUT_DIR/cortexInput.hdf5"}

        thalamus_input = {"input_type": "spikes",
                          "module": "h5",
                          "input_file": "$INPUT_DIR/thalamusInput.hdf5"}
        sim_conf["inputs"] = {"cortexInput": cortex_input,
                              "thalamusInput": thalamus_input}

        out_conf_file = os.path.join(self.out_dir, "simulation_config.json")
        print(f"Writing {out_conf_file}")

        with open(out_conf_file, 'wt') as f:
            json.dump(sim_conf, f, indent=4)

    ############################################################################

    def copy_morphologies(self):
        from shutil import copyfile
        import os

        self.morph_location_lookup = dict([])

        print("Copying morphologies")

        morph_set = set([n["morphology"] for n in self.snudda_load.data["neurons"]])

        for morph_path in morph_set:
            morph_file = snudda_parse_path(morph_path, snudda_data=self.snudda_load.data["SnuddaData"])
            base_name = os.path.basename(morph_file)
            dest_file = os.path.join(self.out_dir, "components", "morphologies", base_name)

            if self.debug:
                print(f"Copying {morph_file} to {dest_file}")

            copyfile(morph_file, dest_file)
            self.morph_location_lookup[morph_path] = dest_file

    ############################################################################

    def copy_hoc(self):
        from shutil import copyfile
        import os

        self.hoc_location_lookup = dict([])

        print("Copying hoc files...")

        missing_files = []

        hoc_set = set([n["hoc"] for n in self.snudda_load.data["neurons"] if n["hoc"]])

        for hoc_path in hoc_set:
            hoc_file = snudda_parse_path(hoc_path, snudda_data=self.snudda_load.data["SnuddaData"])
            if os.path.isfile(hoc_file):
                base_name = os.path.basename(hoc_file)
                dest_file = os.path.join(self.out_dir, "components", "hoc_templates", base_name)

                if self.debug:
                    print(f"Copying {hoc_file} to {dest_file}")

                self.hoc_location_lookup[hoc_path] = dest_file
                copyfile(hoc_file, dest_file)
            else:
                if hoc_file not in missing_files:
                    # Only print same error message ones
                    print(f"- Missing hoc file: {hoc_file}")

                missing_files.append(hoc_file)

    ############################################################################

    def copy_mechanisms(self):
        from shutil import copyfile
        from glob import glob
        import os

        print("Copying mechanisms")
        mech_path = snudda_parse_path(os.path.join("$SNUDDA_DATA", "neurons", "mechanisms"),
                                      snudda_data=self.snudda_load.data["SnuddaData"])

        for mech in glob(os.path.join(mech_path, "*.mod")):
            if self.debug:
                print(f"Copying {mech}")

            mech_file = os.path.basename(mech)
            copyfile(mech, os.path.join(self.out_dir, "components", "mechanisms", mech_file))

    ############################################################################

    def sort_input(self, nl, node_type_id_lookup, input_name=None):

        if self.input_file is None or not os.path.isfile(self.input_file):
            print("No input file has been specified!")
            return None

        max_input = 1000000

        input_mat = np.zeros((max_input, 2))
        input_ctr = 0

        if input_name is not None:
            print(f"Processing inputs {input_name}")
        else:
            print("Processing all virtual inputs")

        with h5py.File(self.input_file) as f:

            try:
                for inp in f["input"]:

                    if input_name is not None and input_name != inp:
                        # Does not match required inputName
                        continue

                    if "activity" in f["input"][inp]:
                        # This is a virtual neuron, save it
                        gid = node_type_id_lookup[inp]

                        for spikeTime in f["input"][inp]["activity"]["spikes"]:
                            input_mat[input_ctr, :] = [spikeTime, gid]
                            input_ctr += 1

                            if input_ctr >= max_input:
                                print(f"Expanding input matrix to {max_input}")
                                max_input *= 2
                                new_input_mat = np.zeros((max_input, 2))
                                new_input_mat[:input_ctr, :] = input_mat
                                input_mat = new_input_mat
            except:
                import traceback
                tstr = traceback.format_exc()
                print(tstr)
                import pdb
                pdb.set_trace()

        print("About to sort spikes in order")

        sort_idx = np.argsort(input_mat[:input_ctr, 0])
        input_matrix = input_mat[sort_idx, :]

        return input_matrix

        ############################################################################

    ############################################################################


if __name__ == "__main__":

    if len(sys.argv) > 1:
        networkFile = sys.argv[1]
    else:
        networkFile = "last"

    if len(sys.argv) > 2:
        inputFile = sys.argv[2]
    else:
        inputFile = None

    if len(sys.argv) > 3:
        outDir = sys.argv[3]
    else:
        outDir = "striatumSim/"

    cn = ExportSonata(network_file=networkFile,
                      input_file=inputFile,
                      out_dir=outDir)

    print("!!! VERIFY THAT SECTION DATA IS CORRECT")
    # cn.plotSectionData()
