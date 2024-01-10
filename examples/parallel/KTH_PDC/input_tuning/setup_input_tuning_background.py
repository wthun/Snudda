import os
import ast
from snudda.input.input_tuning import InputTuning

print("Starting setup_input_tuning.py")

# Should be set by script calling setup_input_tuning_dspn
# os.environ["SNUDDA_DATA"] = "../../../../../BasalGangliaData/data/"


assert os.path.isdir(os.getenv("SNUDDA_DATA")), f"You need to have BasalGangliaData installed for this example."

if os.getenv("SNUDDA_TUNE_NEURON"):
    neuron_type = os.getenv("SNUDDA_TUNE_NEURON")
else:
    neuron_type = "dspn"

if os.getenv("SEED_LIST"):
    seed_list = ast.literal_eval(os.getenv("SEED_LIST"))
else:
    seed_list = None
    
print(f"Optimising input for neuron type {neuron_type}")

network_path = os.path.join("networks", f"input_tuning_{neuron_type}_background")
input_tuning = InputTuning(network_path)

print("Constructor done, calling setup_network.")

neurons_path = os.path.join("$DATA", "neurons", "striatum")
input_tuning.setup_network(neurons_path=neurons_path, 
                           num_replicas=31,
                           neuron_types=neuron_type)

print("Calling setup_input")


input_tuning.setup_background_input(input_types=["cortical_background", "thalamic_background"],
                                    input_density=["1.15*0.05/(1+exp(-(d-30e-6)/5e-6))", "0.05*exp(-d/200e-6)"],
                                    input_fraction=[0.5, 0.5],
                                    num_input_min=10, num_input_max=1000,
                                    input_frequency=[2, 2], input_duration=10,
                                    generate_input=seed_list is None)

if seed_list is not None:
    original_input = input_tuning.input_spikes_file

    for ctr, seed in enumerate(seed_list):
        print(f"Iteration: {ctr + 1}/{len(seed_list)} (seed: {seed})")
        input_tuning.regenerate_input(seed=seed, use_meta_input=False)

print("All done with setup_input_tuning.py")

