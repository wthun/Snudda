from snudda.simulate.simulate import SnuddaSimulate
import snudda.neuromodulation.modulation as modulation
import snudda.neuromodulation.translator as translator
from snudda.simulate.nrn_simulator_parallel import NrnSimulatorParallel
from snudda.utils.snudda_path import snudda_parse_path
from snudda.neurons.neuron_model_extended import NeuronModel
from snudda.utils.load import SnuddaLoad
from neuron import h
import json
import numpy as np
import h5py


class SnuddaSimulateNeuromodulationSynapse(SnuddaSimulate):

    def __init__(self,
                 network_path=None,
                 network_file=None,
                 input_file=None,
                 verbose=False,
                 log_file=None,
                 disable_gap_junctions=True,
                 simulation_config=None, neuromodulator_description=None,
                 neuromodulation_weight=None):

        self.neuromodulator_description = neuromodulator_description
        self.neuromodulation = dict()
        self.current_cell = None
        self.syn_gpcrs = list()
        self.cell_modulator = dict()
        self.neuromodulation_weight = neuromodulation_weight
        self.connector = [*self.neuromodulator_description.keys()]

        super(SnuddaSimulateNeuromodulationSynapse, self).__init__(network_path=network_path,
                                                                   network_file=network_file,
                                                                   input_file=input_file,
                                                                   verbose=verbose,
                                                                   log_file=log_file,
                                                                   disable_gap_junctions=disable_gap_junctions,
                                                                   simulation_config=simulation_config)

        # Change the self.custom_setup from None, and execute the custom setup code within this file
        self.cell_type_ion_channels_per_section = self.ion_channels_per_section()
        self.key_list = self.modulation_keys()

    def setup(self):

        self.check_memory_status()
        self.distribute_neurons()
        self.setup_neurons()
        self.check_memory_status()
        self.pc.barrier()

        # Neuromodulation requires this to be run, before connect_network
        self.neuromodulation_setup()

        self.connect_network()
        self.check_memory_status()
        self.pc.barrier()


    def modulation_keys(self):

        key_list = list()
        # TODO: if the setup-file gets rewritten, then this would turn into self...descr..keys() # add module key for concACh
        for connector_info in self.neuromodulator_description.values():
            key_list.append('level' + connector_info['key'])
            key_list.append('mod' + connector_info['key'])
        return key_list

    def ion_channels_per_section(self):

        cell_type = dict()

        for syn in self.connector:

            # it would work here as well, as you are only iterating through self.neuro..descrip

            for cell_type_name in self.neuromodulator_description[syn]['cells'].keys():

                cell_type.update({cell_type_name: dict()})

                for tpart in self.neuromodulator_description[syn]['cells'][cell_type_name]['ion_channels'].keys():

                    if tpart in cell_type[cell_type_name].keys():
                        cell_type[cell_type_name][tpart] = cell_type[cell_type_name][tpart] + \
                                                          self.neuromodulator_description[syn]['cells'][cell_type_name][
                                                              'ion_channels'][tpart]
                    else:
                        cell_type[cell_type_name].update({tpart: self.neuromodulator_description[syn]['cells'][
                            cell_type_name]['ion_channels'][tpart]})

        return cell_type

    def get_ion_channels_per_section(self, cell_type):
        return self.cell_type_ion_channels_per_section[cell_type]

    def organise_cell_info(self, cell_modulator):

        # if it is send as a list already, then you have access to the sections and you dont need to rearange the information

        cell_information = dict()

        for cell, instructions in cell_modulator.items():

            for i in range(len(instructions['dend_compartment'])):

                dendcomp = instructions['dend_compartment'][i]

                if dendcomp in cell_information.keys() and instructions['section_dist'][i] in cell_information[dendcomp]['section_dist']:

                    cell_information[dendcomp]['section_dist'].append(instructions['section_dist'][i])
                    cell_information[dendcomp]['connection'][str(instructions['section_dist'][i])].append(
                        instructions['synapse'][i])
                    cell_information[dendcomp]['precell'][str(instructions['section_dist'][i])].append(
                        instructions['precell'][i])
                elif dendcomp in cell_information.keys() and instructions['section_dist'][i] not in cell_information[dendcomp]['section_dist']:

                    cell_information[dendcomp]['section_dist'].append(instructions['section_dist'][i])
                    cell_information[dendcomp]['connection'].update(
                        {str(instructions['section_dist'][i]): [instructions['synapse'][i]]})
                    cell_information[dendcomp]['precell'].update(
                        {str(instructions['section_dist'][i]): [instructions['precell'][i]]})

                else:
                    cell_information.update({dendcomp: {'section_dist': [instructions['section_dist'][i]],
                                                        'connection':
                                                        {str(instructions['section_dist'][i]): [instructions['synapse'][i]]},
                                                        'precell': {str(instructions['section_dist'][i]): [
                                                         instructions['precell'][i]]}}})

            cell_type_name = str(cell_modulator[cell]['cell_name']).split('_')[0]

        self.implement_modulation(cell_information, cell_type_name)

    def add_gpcrs_in_cell_segments(self, cell_information):

        added_synapses = dict()
        # Add all the gpcrs to the segments which have been marked in this cell

        for syn_in_section in self.connector:

            for sec, sec_info in cell_information.items():

                for seg in sec:

                    synapse_gpcr = getattr(self.sim.neuron.h, syn_in_section)(seg)

                    self.syn_gpcrs.append(synapse_gpcr)

                    if str(seg.x) in sec_info['precell'].keys():

                        for cell_id_source in sec_info['precell'][str(seg.x)]:
                            nc = self.pc.gid_connect(cell_id_source, synapse_gpcr)
                            nc.weight[0] = self.neuromodulation_weight
                            nc.delay = self.synapse_delay
                            nc.threshold = self.spike_threshold

                            self.net_con_list.append(nc)
                    self.synapse_list.append(synapse_gpcr)

                    if sec in added_synapses.keys() and str(seg.x) in added_synapses[sec].keys():

                        added_synapses[sec][str(seg.x)].update({syn_in_section: synapse_gpcr})

                    elif sec in added_synapses.keys() and str(seg.x) not in added_synapses[sec].keys():

                        added_synapses[sec].update({str(seg.x): {syn_in_section: synapse_gpcr}})
                    else:
                        added_synapses.update({sec: {str(seg.x): {syn_in_section: synapse_gpcr}}})

        return added_synapses

    def implement_modulation(self, cell_information, cell_type_name):

        cell_added_synapses = self.add_gpcrs_in_cell_segments(cell_information=cell_information)

        ion_channels_per_section = self.get_ion_channels_per_section(cell_type_name)

        for sec, sec_info in cell_information.items():

            tpart = translator.re_translation[sec.name().split('.')[-1].split('[')[0]]

            ion_channels = ion_channels_per_section[tpart]

            for mechanism_name, values in sec.psection()['density_mechs'].items():

                mechanism_name_ptr = mechanism_name + "_ptr"

                if mechanism_name in ion_channels:

                    level_list = [type_level for type_level in [*sec.psection()['density_mechs'][mechanism_name].keys()] if 'level' in type_level]
                    mod_key_list = [f"mod{n.replace('level','')}_{mechanism_name_ptr}" for n in level_list]
                    sec.insert(mechanism_name_ptr)

                    for syn in self.connector:

                        for segment in sec:
                            seg_x_str = str(segment.x)
                            # TODO: change the segment.x to not string
                            pointer = cell_added_synapses[sec][seg_x_str][syn]._ref_concentration
                            # talk to NEURON maybe they can help change this, so you don't have to replace the mechanisms, crashes with uninitialised pointers
                            for neurotransmitter_level, mod_key in zip(level_list, mod_key_list):

                                setattr(segment, mod_key, 1)
                                self.sim.neuron.h.setpointer(pointer, neurotransmitter_level,
                                                             getattr(segment, mechanism_name_ptr))

                    # Parameterize the pointer version of density_mech, skip level and mod, as that would turn off modulation
                    for param, val in values.items():
                        for i, segmentet in enumerate(sec):
                            if param not in self.key_list:
                                setattr(segmentet, '_'.join([param, mechanism_name_ptr]), val[i])

            for mech_name in ion_channels:
                sec.uninsert(mech_name)

    def add_gpcr_synapse(self, channel_module, cell_id_source, dend_compartment, section_dist):

        cell = dend_compartment.cell()

        if self.current_cell is None:

            self.current_cell = cell
            self.cell_modulator = dict()
            self.cell_modulator.update({cell: dict()})
            self.cell_modulator[cell].update({'precell': list(),
                                              'postcell': list(),
                                              'dend_compartment': list(),
                                              'section_dist': list(),
                                              'method': list(),
                                              'key': list(),
                                              'index': list(),
                                              'synapse': list(),
                                              'cell_name': dend_compartment.cell()})

        elif self.current_cell != cell:

            self.current_cell = None
            self.organise_cell_info(self.cell_modulator)

        else:

            syn_name = str(channel_module).split('()')[0]
            postcell_name = str(dend_compartment.cell()).split('_')[0]
            # add secondary look-up concACh to ACh to get correct key for self.neuro...descr..
            if postcell_name in self.neuromodulator_description[syn_name]["cells"].keys():
                self.cell_modulator[cell]['precell'].append(cell_id_source)
                self.cell_modulator[cell]['postcell'].append(postcell_name)
                self.cell_modulator[cell]['dend_compartment'].append(dend_compartment)
                self.cell_modulator[cell]['section_dist'].append(section_dist)
                self.cell_modulator[cell]['method'].append(self.neuromodulator_description[syn_name]["cells"][postcell_name])
                self.cell_modulator[cell]['key'].append(self.neuromodulator_description[syn_name])
                self.cell_modulator[cell]['synapse'].append(channel_module)

    def add_mark_gpcr(self, cell_id_source, dend_compartment, section_dist, synapse_type_id):

        if section_dist == 0.0:
            section_dist = 0.01
        if section_dist == 1.0:
            section_dist = 0.99

        (channel_module, par_data) = self.synapse_parameters[synapse_type_id]

        if str(channel_module).split('()')[0] in self.connector:
            self.add_gpcr_synapse(channel_module, cell_id_source, dend_compartment, section_dist)

    def neuromodulation_setup(self):

        # This loops through all the synapses, and connects the relevant ones
        # nextRowSet = [ fromRow, toRow ) -- ie range(fromRow,toRow)
        next_row_set = self.find_next_synapse_group(next_row=0)

        while next_row_set is not None:
            # Add the synapses to the neuron
            self.connect_neuron_synapses_gpcr(start_row=next_row_set[0], end_row=next_row_set[1])

            # Find the next group of synapses
            next_row_set = self.find_next_synapse_group(next_row_set[1]) # 2nd number was not included in range

    def connect_neuron_synapses_gpcr(self, start_row, end_row):

        source_id_list, dend_sections, sec_x, synapse_type_id, axon_distance, conductance, parameter_id = \
            self.get_synapse_info(start_row=start_row, end_row=end_row)

        # Filter away synapse_type_id not conc* connector and check if the start and end row defines the cell,
        # hence you can send all information to add_gpcr_synapse
        for (src_id, section, section_x, s_type_id, axon_dist, cond, p_id) \
                in zip(source_id_list, dend_sections, sec_x, synapse_type_id,
                       axon_distance, conductance, parameter_id):

            try:
                # !!!
                self.add_mark_gpcr(cell_id_source=src_id,
                                   dend_compartment=section,
                                   section_dist=section_x,
                                   synapse_type_id=s_type_id)
            except:
                import traceback
                tstr = traceback.format_exc()
                self.write_log(tstr, is_error=True)
                import pdb
                pdb.set_trace()

    def get_synapse(self, channel_module, dend_compartment, section_dist):

        syn = None
        # add lookup on channel_module to skip split
        if str(channel_module).split('()')[0] not in self.connector:

            channel_module_p = eval(f"self.sim.neuron.h.{str(channel_module).split('()')[0]}_ptr")

            for point_process_in_section in dend_compartment(section_dist).point_processes():

                if str(point_process_in_section).split('[')[0] in self.connector:

                    syn = channel_module_p(dend_compartment(section_dist))

                    level = [x for x in dir(syn) if 'level' in x]

                    pointer = point_process_in_section._ref_concentration

                    for neurotransmitter_key in level:
                        # remove this parameter by setting default 1
                        setattr(syn, f"mod{neurotransmitter_key.replace('level', '')}", 1)
                        self.sim.neuron.h.setpointer(pointer, neurotransmitter_key, syn)

        if syn is None:
            syn = channel_module(dend_compartment(section_dist))

        return syn

    def get_external_input_synapse(self, channel_module, section, section_x):

        syn = None

        for point_process_in_section in section(section_x).point_processes():

            if str(point_process_in_section).split('[')[0] in self.connector and str(channel_module).split('()')[0] not in self.connector:

                channel_module_p = eval('self.sim.neuron.h.' + str(channel_module).split('()')[0] + "_ptr")

                syn = channel_module_p(section(section_x))

                level = [x for x in dir(syn) if 'level' in x]

                pointer = point_process_in_section._ref_concentration

                for neurotransmitter_key in level:
                    setattr(syn, 'mod' + neurotransmitter_key.replace('level', ''), 1)
                    self.sim.neuron.h.setpointer(pointer, neurotransmitter_key, syn)
        if syn is None:
            syn = channel_module(section(section_x))

        return syn

