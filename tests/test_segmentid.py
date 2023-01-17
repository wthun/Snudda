import unittest
import os

import neuron
import numpy as np

from snudda.utils import snudda_parse_path
from snudda.simulate.nrn_simulator_parallel import NrnSimulatorParallel

import bluepyopt.ephys as ephys
from snudda.neurons.neuron_model_extended import NeuronModel
from snudda.neurons.neuron_morphology_extended import NeuronMorphologyExtended


class SegmentIdTestCase(unittest.TestCase):

    """NEURON renumberes all the branches into segments with separate id. This code verifies that Snudda's algorithm
       replicates what NEURON does internally. It will also warn if NEURON's algorithm at some point changes.

        If you want to test all morphologies in BasalGangliaData, first set SNUDDA_DATA:

        export SNUDDA_DATA=/home/hjorth/HBP/BasalGangliaData/data/
        python3 -m unittest test_segmentid.py

    """

    def setUp(self) -> None:

        if os.path.dirname(__file__):
            os.chdir(os.path.dirname(__file__))

        self.sim = NrnSimulatorParallel(cvode_active=False)

    def test_segment_id_numbering(self, morph_file=None):

        # Load morphology into Snudda
        if not morph_file:
            print("No morphology file specified, skipping.")
            return

        print(f"Loading neuron {morph_file}")
        snudda_neuron = NeuronMorphologyExtended(name="fs", swc_filename=morph_file) # -- , use_cache=False) -- not implemented at the moment

        soma_position = snudda_neuron.morphology_data["neuron"].sections[1][0].position
        self.assertTrue((soma_position == 0).all(), f"Soma should be centered for {morph_file}.")

        # Load morphology into NEURON
        neuron_model = NeuronModel(param_file=os.path.join("data", "fake-parameters.json"),
                                   morph_path=morph_file,
                                   mech_file=os.path.join("data", "fake-mechanisms.json"),
                                   cell_name="fs",
                                   modulation_file=None,
                                   parameter_id=0,
                                   modulation_id=0)

        neuron_model.instantiate(sim=self.sim)

        ax = None
        error_cutoff = 10
        
        for sec in snudda_neuron.morphology_data["neuron"].sections[3].values():

            neuron_sec = neuron_model.map_id_to_compartment(section_id=[sec.section_id])[0]
            n_points = int(neuron_sec.n3d())
            arc_dist = np.array([neuron_sec.arc3d(x) for x in range(0, n_points)])
            norm_arc_dist = arc_dist / arc_dist[-1]

            for sec_x, pos in zip(sec.section_x, sec.position * 1e6):
                closest_idx = np.argmin(np.abs(norm_arc_dist - sec_x))

                x_ref = neuron_sec.x3d(closest_idx)
                y_ref = neuron_sec.y3d(closest_idx)
                z_ref = neuron_sec.z3d(closest_idx)

                x, y, z = pos

                self.assertTrue(np.linalg.norm([x - x_ref, y - y_ref, z - z_ref]) < error_cutoff,
                                f"Error parsing {morph_file}. Snudda sec_id {sec.section_id}, sec_x {sec_x} has xyz = {pos}\n"
                                f"NEURON sec_id {sec.section_id}, sec_x {norm_arc_dist[closest_idx]} has xyz = {x_ref}, {y_ref}, {z_ref}\n"
                                f"Distance: {np.linalg.norm([x - x_ref, y - y_ref, z - z_ref])} micrometer")

    def test_neurons_in_folder(self, neuron_dir=None):

        import glob

        if not neuron_dir:
            print("No neuron dir given, skipping.")
            return

        n_dirs = glob.glob(os.path.join(neuron_dir, '*'))

        # In case the user gave the neuron directory with SWC files
        swc_files = glob.glob(os.path.join(neuron_dir, '*swc'))

        for n_dir in n_dirs:

            swc_files1 = glob.glob(os.path.join(neuron_dir, n_dir, '*swc'))
            for swc_f in swc_files1:
                swc_files.append(os.path.join(neuron_dir, n_dir, swc_f))

            swc_files2 = glob.glob(os.path.join(neuron_dir, n_dir, "morphology", '*swc'))
            for swc_f in swc_files2:
                swc_files.append(os.path.join(neuron_dir, n_dir, "morphology", swc_f))

        for swc_file in swc_files:
            with self.subTest(msg=f"Testing {swc_file}"):
                self.test_segment_id_numbering(morph_file=swc_file)

    def test_all_dir(self):

        neuron_dirs = [snudda_parse_path(os.path.join("$SNUDDA_DATA", "neurons", "striatum", "dspn"), snudda_data=None),
                       snudda_parse_path(os.path.join("$SNUDDA_DATA", "neurons", "striatum", "ispn"), snudda_data=None),
                       snudda_parse_path(os.path.join("$SNUDDA_DATA", "neurons", "striatum", "fs"), snudda_data=None),
                       snudda_parse_path(os.path.join("$SNUDDA_DATA", "neurons", "striatum", "lts"), snudda_data=None),
                       ]

        for neuron_dir in neuron_dirs:
            self.test_neurons_in_folder(neuron_dir=neuron_dir)


if __name__ == '__main__':
    unittest.main()
