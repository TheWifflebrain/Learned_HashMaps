import pandas as pd
import tensorflow as tf
import tensorflow_lattice as tfl
import logging
logging.getLogger().setLevel(logging.INFO)
from collections import defaultdict, Counter
#import matplotlib.pyplot as plt
import sys
import shutil
import argparse
import os

global data_dir
data_dir = './'
global num_features
num_features = 9
global use_lattice
use_lattice = True
global lr
lr = 0.001

output_dir = "results/"
quantiles_dir = "quantiles/"
CSV_COLUMNS = ["feature" + str(i) for i in range(1, num_features + 1)] + ["value"]

def get_test_input_fn():
  return get_input_fn(data_dir + ".test", batch_size=10000, num_epochs=1, shuffle=False)

def get_val_input_fn():
  return get_input_fn(data_dir + ".val", batch_size=10000, num_epochs=1, shuffle=False)
    
def get_train_input_fn(batch_size=10000, num_epochs=1, shuffle=False):
  return get_input_fn(data_dir + ".train", batch_size, num_epochs, shuffle)

def get_input_fn(file_path, batch_size, num_epochs, shuffle):
  df_data = pd.read_csv(
      tf.gfile.Open(file_path),
      names=CSV_COLUMNS,
      skipinitialspace=True,
      engine="python",
      skiprows=1)
  labels = df_data["value"]
  return tf.estimator.inputs.pandas_input_fn(
      x=df_data,
      y=labels,
      batch_size=batch_size,
      shuffle=shuffle,
      num_epochs=num_epochs,
      num_threads=1)

def create_feature_columns():
  # Categorical features.
  print("Num features:", num_features)
  return [tf.feature_column.numeric_column("feature" + str(i)) for i in range(1, num_features + 1)]

def create_quantiles(quantiles_dir):
    """Creates quantiles directory if it doesn't yet exist."""
    input_fn = get_test_input_fn()
    print(create_feature_columns())
    tfl.save_quantiles_for_keypoints(
        input_fn=input_fn,
        save_dir=quantiles_dir,
        feature_columns=create_feature_columns(),
        num_steps=None)

def create_calibrated_linear(feature_columns, config, quantiles_dir):
    feature_names = [fc.name for fc in feature_columns]
    hparams = tfl.CalibratedLinearHParams(
                feature_names=feature_names,
                num_keypoints=200,
                learning_rate=lr)
    hparams.set_feature_param("feature1", "monotonicity", 1)
    return tfl.calibrated_linear_regressor(
            feature_columns=feature_columns,
            model_dir=config.model_dir,
            config=config,
            hparams=hparams,
            quantiles_dir=quantiles_dir)

def create_calibrated_rtl(feature_columns, config, quantiles_dir):
  feature_names = [fc.name for fc in feature_columns]
  hparams = tfl.CalibratedRtlHParams(
      feature_names=feature_names,
      num_keypoints=200,
      learning_rate=lr,
      lattice_l2_laplacian_reg=5.0e-4,
      lattice_l2_torsion_reg=1.0e-4,
      lattice_size=2,
      lattice_rank=4,
      num_lattices=10)
  return tfl.calibrated_rtl_classifier(
      feature_columns=feature_columns,
      model_dir=config.model_dir,
      config=config,
      hparams=hparams,
      quantiles_dir=quantiles_dir)

def create_calibrated_lattice(feature_columns, config, quantiles_dir):
    feature_names = [fc.name for fc in feature_columns]
    hparams = tfl.CalibratedLatticeHParams(
                feature_names=feature_names,
                num_keypoints=200,
                lattice_l2_laplacian_reg=5e-4,
                lattice_l2_torsion_reg=1e-4,
                learning_rate=lr,
                lattice_size=2)
    hparams.set_feature_param("feature1", "monotonicity", 1)
    return tfl.calibrated_lattice_classifier(
            feature_columns=feature_columns,
            model_dir=config.model_dir,
            config=config,
            hparams=hparams,
            quantiles_dir=quantiles_dir)

def create_estimator(config, quantiles_dir):
    """Creates estimator for given configuration based on --model_type."""
    feature_columns = create_feature_columns()
    if use_lattice:
        return create_calibrated_lattice(feature_columns, config, quantiles_dir)
    else:
        return create_calibrated_linear(feature_columns, config, quantiles_dir)

def calculate_collision_rate(estimator, input_fn, N):
    results = estimator.predict(input_fn=input_fn)
    buckets = defaultdict(int)
    for result in results:
        if use_lattice:
            bucket = min(N - 1, max(0, int(result['logistic'][0]*N)))
        else:
            bucket = min(N - 1, max(0, int(result['predictions'][0]*N)))
        buckets[bucket] += 1

    num_collisions, buckets_used, avg = 0, 0, 0
    cnt = Counter()
    for i in range(N):
        if buckets[i] > 1:
            num_collisions += 1
            avg += buckets[i]
        if buckets[i] > 0:
            buckets_used += 1
            cnt[buckets[i]] += 1
            print("bucket %d, number of entries: %d" % (i, buckets[i]))

    print("collision rate:", num_collisions/buckets_used)
    print("average number of entries in a bad bucket:", avg/num_collisions)
    print(cnt)

def main(args):
    create_quantiles(quantiles_dir)

    # Create config and then model.
    config = tf.estimator.RunConfig().replace(model_dir=output_dir)
    estimator = create_estimator(config, quantiles_dir)
   
    for epoch in range(5):
        print("Epoch %d" % epoch)
        estimator.train(input_fn=get_train_input_fn(batch_size=64, num_epochs=1, shuffle=True))
        print("training stats:")
        calculate_collision_rate(estimator, get_train_input_fn(), 60000)
        #print("validation stats:")
        #calculate_collision_rate(estimator, get_val_input_fn(), 20000)
        print("test stats:")
        calculate_collision_rate(estimator, get_test_input_fn(), 20000)
  

parser = argparse.ArgumentParser(description='Training')
parser.add_argument('-data_dir')
parser.add_argument('-num_features', type=int, default=1)
parser.add_argument('-lr', type=float, default=0.001)
parser.add_argument('-use_lattice', action='store_true')
args = parser.parse_args()

data_dir = args.data_dir
num_features = args.num_features
use_lattice = args.use_lattice
lr = args.lr
if os.path.exists(output_dir):
  shutil.rmtree(output_dir)
main(args)
