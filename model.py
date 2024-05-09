import os, shutil, string, csv, subprocess, logging, random
from datetime import datetime
os.environ['OMP_NUM_THREADS'] = '1'
import numpy as np
import pandas as pd
import nibabel as nib
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import Model
from tensorflow.keras.losses import MeanSquaredError
from tensorflow.keras.utils import Sequence
from tensorflow.keras.models import load_model
from tensorflow.keras.optimizers import Adam, schedules
from tensorflow.keras.callbacks import EarlyStopping
from antspynet.architectures import create_convolutional_autoencoder_model_3d

# LOCAL IMPORTS
from preproc import BaseT1CAE
from utils import exceptions

logger = logging.getLogger(__name__)


class DataGenerator(Sequence):
    
    '''Custom data generator to handle 4D batches.'''
    def __init__(self, batch_size, mode, shuffle=True):
        super(DataGenerator, self).__init__()
        self.base_dir = os.getcwd()
        self.input_data_dir = os.path.join(self.base_dir, 'data')
        self.train_data_dir = os.path.join(self.input_data_dir, 'training')
        self.test_data_dir = os.path.join(self.input_data_dir, 'testing')
        self.mode = mode
        self.data_shape = (180, 220, 180, 1)
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.filenames = self.get_imgs_by_mode()
        self.indexes = list(range(len(self.filenames)))
        self.on_epoch_end()
        if self.shuffle:
            random.shuffle(self.indexes)

    def __len__(self):
        return len(self.filenames)//self.batch_size
 
    def get_imgs_by_mode(self):
        if self.mode == 'training':
            imgs = os.listdir(self.train_data_dir)
        elif self.mode == 'testing':
            imgs = os.listdir(self.test_data_dir)
        return imgs
    
    def __getitem__(self, index):
        # x=y for CAE task
        start_idx = index * self.batch_size
        end_idx = (index+1) * self.batch_size
        batch_indexes = self.indexes[start_idx:end_idx]
        batch_filenames = [self.filenames[i] for i in batch_indexes]
        x = np.empty((len(batch_indexes), *self.data_shape))
        y = np.empty((len(batch_indexes), *self.data_shape))
        for i, idx in enumerate(batch_indexes):
            image = os.path.join(self.input_data_dir, self.mode, batch_filenames[i])
            img = nib.load(image)
            data = img.get_fdata()
            # resize from (182,218,182) --> (180,220,180) for network compatibility
            data = np.pad(data, pad_width=((0,0),(1,1),(0,0)), mode='constant', constant_values=0)
            data = data[1:181,:,1:181]
            # min-max scale data from range [0,255] --> [0,1] for training stability
            data = data/255
            x[i,:,:,:,0] = data
            y[i,:,:,:,0] = data
        return x, y

    def on_epoch_end(self):
        if self.shuffle:
            random.shuffle(self.indexes)


class T1CAEModel(BaseT1CAE):

    '''AI model using ANTsPyNet convolutional autoencoder'''

    def __init__(self, batch_size, epochs, fmap_size):
        super(T1CAEModel, self).__init__()
        self.input_shape = (180, 220, 180, 1)
        self.batch_size = batch_size
        self.epochs = epochs
        self.fmap_size = fmap_size
        # mixed precision for training trades computation time for memory
        policy = tf.keras.mixed_precision.Policy('mixed_float16')
        tf.keras.mixed_precision.set_global_policy(policy)

    def build_model(self):
        # use ANTsPyNet 3D CAE
        autoencoder, encoder = create_convolutional_autoencoder_model_3d(
                input_image_size=self.input_shape,
                number_of_filters_per_layer=(32,64,128,self.fmap_size),
                convolution_kernel_size=(5,5,5),
                deconvolution_kernel_size=(5,5,5))
        inputdata = autoencoder.input
        features = encoder.output
        reconstructed = autoencoder.output
        print(inputdata.shape)
        print(reconstructed.shape)
        self.compile_model(autoencoder)
        return autoencoder

    def compile_model(self, model): 
        mse = MeanSquaredError(reduction='sum_over_batch_size', name='MSE')
        lr_schedule = schedules.ExponentialDecay(initial_learning_rate=0.001, decay_steps=1000, decay_rate=0.9)
        opt = Adam(learning_rate=lr_schedule)
        model.compile(loss=mse, optimizer=opt, metrics=[mse])

    def train_model(self, model, save=False):
        train_generator = DataGenerator(batch_size=self.batch_size, mode='training')
        cb = EarlyStopping(monitor='loss', verbose=1, patience=3, start_from_epoch=10)
        if save == True:
            train_history = model.fit(train_generator, epochs=self.epochs, callbacks=cb)
            return train_history
        else:
            model.fit(train_generator, epochs=self.epochs, callbacks=cb)

    def test_model(self, model):
        test_generator = DataGenerator(batch_size=self.batch_size, mode='testing')
        loss, metric = model.evaluate(test_generator)
        print(f'Testing loss: {loss:.8f}')
        #print(f'Testing MSE: {metric:.8f}')

    def extract_features(self, model):
        generator = DataGenerator(batch_size=self.batch_size, mode='')
        features = model.predict(generator)

    def plot_train_progress(self, train_history):
        pass


if __name__ == '__main__':
    t1cae_model = T1CAEModel(batch_size=15, epochs=50, fmap_size=50)
    model = t1cae_model.build_model()
    print(model.summary())
    t1cae_model.train_model(model)
    t1cae_model.test_model(model)

