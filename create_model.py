#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Jul  1 14:58:18 2020

@author: jaisi8631
"""

# ------------------
# REGULAR IMPORTS
# ------------------
import pickle
import pandas as pd
import numpy as np
import math
from scipy import signal
import matplotlib.pyplot as plt

from sklearn.metrics import confusion_matrix
from sklearn.metrics import precision_recall_fscore_support
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import StandardScaler
from sklearn.utils import class_weight
from sklearn import decomposition
from sklearn.model_selection import GridSearchCV

import keras
from keras.utils.vis_utils import plot_model
from keras.models import Model 
from keras.optimizers import Adam
from keras.models import load_model
from keras.callbacks import ModelCheckpoint
from keras.callbacks import EarlyStopping
from keras.wrappers.scikit_learn import KerasClassifier


# ------------------
# LSTM IMPORTS
# ------------------
from keras.models import Sequential
from keras.layers import Dense
from keras.layers import LSTM


# ------------------
# CNN IMPORTS
# ------------------
from keras.optimizers import SGD
from keras.layers import Reshape
from keras.layers import Conv1D
from keras.layers import InputLayer
from keras.layers import Dropout
from keras.layers import Flatten
from keras.layers import MaxPooling1D
from keras.layers import GlobalAveragePooling1D


# ------------------
# CONSTANTS
# ------------------
START_TIME_INTERICTAL = 407898590000
END_TIME_INTERICTAL = 407998350000
START_TIME_ICTAL = 416039606029
END_TIME_ICTAL = 416112464960
FS = 1024
DOWN_SAMPLE_FACTOR = 10
STEP_SIZE = 256
SEQUENCE_LEN = 1024
SEQUENCE_PCA = 1000

TRAIN_SIZE = 0.8
TEST_SIZE = 0.1
VAL_SIZE = 0.1

EPOCHS = 5
BS = 128
LR = 0.01
NUM_CLASSES = 2
MOMENTUM = 0.1
DECAY = 1e-6

GS_EPOCHS = [1, 2]
GS_BS = [32, 64]
GS_OPTIMIZERS = ['adam', 'rmsprop']

ADAM_DEFAULT = 'adam'
SGD_DEFAULT = 'sgd'
ADAM_CUSTOM = Adam(lr = LR)
SGD_CUSTOM = SGD(lr = LR, momentum = MOMENTUM, decay = DECAY, nesterov = True)

PATH_INTERICTAL = "../datasets/hup138-interictal.pickle"
PATH_ICTAL = "../datasets/hup138-ictal.pickle"


# ------------------
# LOSS HISTORY
# ------------------
class LossHistory(keras.callbacks.Callback):
    def on_train_begin(self, logs={}):
        self.history = {'loss':[],'acc':[]}

    def on_batch_end(self, batch, logs={}):
        self.history['loss'].append(logs.get('loss'))
        self.history['acc'].append(logs.get('acc'))


# ------------------
# LOAD DATA
# ------------------
def get_data(path):
    with open(path, 'rb') as f: data, fs = pickle.load(f)
    labels = pd.read_csv("labels/hup138-labels.csv", header = None)
    labels_list = labels[0].tolist()
    data = data[data.columns.intersection(labels_list)]
    return data

def iEEG_data_filter(data, fs, cutoff1, cutoff2, notch):
	column_names = data.columns
	data = np.array(data)
	number_of_channels = data.shape[1]
    
    # Cut-off frequency of the filter
	fc = np.array([cutoff1, cutoff2])
    # Normalize the frequency
	w = fc / np.array([(fs / 2), (fs / 2)])  
    
	b, a = signal.butter(4, w, 'bandpass')
	filtered = np.zeros(data.shape)
	for i in np.arange(0,number_of_channels):
		filtered[:,i] = signal.filtfilt(b, a, data[:,i])
	filtered = filtered + (data[0] - filtered[0])
	f0 = notch
	q = 30
	b, a = signal.iirnotch(f0, q, fs)
	notched = np.zeros(data.shape)
	for i in np.arange(0, number_of_channels):
		notched[:, i] = signal.filtfilt(b, a, filtered[:, i])
	notched_df = pd.DataFrame(notched, columns=column_names)
	return notched_df


# ------------------
# DATASET OUTLINE
# ------------------
def create_timestamps(data):
    timestamps = []
    for i in range(0, data.shape[0]):
        timestamps.append(START_TIME_ICTAL + (i * FS))
    return timestamps


# ------------------
# PROCESS DATA
# ------------------
def create_merge_dataset(data, split_point, start_time_interictal, 
                         end_time_interictal, start_time_ictal, end_time_ictal):
    dataset = []
    dataset_targets = []
    labels = pd.read_csv("labels/hup138-labels.csv", header = None)
    
    for column in data:
        
        print("Reading data for electrode " + column)
        
        col_list = data[column].tolist()
        col_data = labels.loc[labels[0] == column]
        
        col_start_time = col_data.iat[0, 1]
        col_end_time = col_data.iat[0, 2]
        
        if(col_start_time == '-' or col_end_time == '-'):
            col_start_time = int(start_time_ictal + 1)
            col_end_time = int(start_time_ictal + 1)
        else:
            col_start_time = int(col_start_time)
            col_end_time = int(col_end_time)
            
        # first process interictal data
        for index in range(SEQUENCE_LEN, split_point, STEP_SIZE):
            sequence = col_list[(index - SEQUENCE_LEN) : index]
            sequence = [[i] for i in sequence]
            dataset.append(sequence)
    
            sequence_end_time = start_time_interictal + (index * FS * 10)
            
            if(sequence_end_time >= col_start_time and 
               sequence_end_time <= col_end_time):
                dataset_targets.append(1)
            else:
                dataset_targets.append(0)
        
        # then process ictal data
        for index in range(SEQUENCE_LEN, data.shape[0] - split_point, STEP_SIZE):
            new_index = index + split_point
            sequence = col_list[(new_index - SEQUENCE_LEN) : new_index]
            sequence = [[i] for i in sequence]
            dataset.append(sequence)
    
            sequence_end_time = start_time_ictal + (index * FS * 10)
            
            if(sequence_end_time >= col_start_time and 
               sequence_end_time <= col_end_time):
                dataset_targets.append(1)
            else:
                dataset_targets.append(0)
                
    dataset = np.array(dataset)
    dataset_targets = np.array(dataset_targets)
    
    return dataset, dataset_targets

def scale_data(dataset):
    scaler = StandardScaler()
    dataset = scaler.fit_transform(dataset.reshape(-1, dataset.shape[-1])).reshape(dataset.shape)
    return dataset

def apply_pca(dataset, components):
    pca = decomposition.PCA(n_components = components)
    pca.fit(components)
    dataset = pca.transform(dataset)
    return dataset

def create_class_weights(y_train):
    class_weights = class_weight.compute_class_weight('balanced',
                                                      classes = np.unique(y_train),
                                                      y = y_train)
    class_weight_dict = dict(enumerate(class_weights))
    return class_weight_dict


# ------------------
# LSTM MODEL
# ------------------
def create_lstm_model():
    model = Sequential()
    model.add(LSTM(256, input_shape = (SEQUENCE_LEN, 1), dropout = 0.2, recurrent_dropout = 0.2, return_sequences = True))
    model.add(LSTM(32, dropout = 0.2, recurrent_dropout = 0.2, return_sequences = True))
    model.add(LSTM(32, return_sequences = False))
    model.add(Dense(1, activation = 'sigmoid'))
    print(model.summary())
    plot_model(model, to_file = 'figures/lstm_diagram.png', 
               show_shapes = True, show_layer_names = True)
    return model

def train_lstm_model(model, model_name, x_train, y_train, x_val, y_val):    
    chk = ModelCheckpoint('models/' + model_name + '.pkl', 
                          monitor = 'val_accuracy', 
                          save_best_only = True, 
                          mode = 'max', 
                          verbose = 1)
    lstm_history = LossHistory()
    callbacks_list = [
        chk,
        lstm_history
    ]
    
    model.compile(loss = 'binary_crossentropy', 
                  optimizer = ADAM_CUSTOM, 
                  metrics = ['accuracy'])
    
    class_weights = create_class_weights(y_train)
    
    model.fit(x_train, y_train, 
              class_weight = class_weights,
              epochs = EPOCHS, 
              batch_size = BS, 
              callbacks = callbacks_list, 
              validation_data = (x_val, y_val))
    
    return model, lstm_history


# ------------------
# CNN MODEL
# ------------------
def create_custom_cnn_model():
    model = Sequential()
    model.add(Conv1D(500, 100, activation='relu', input_shape = (SEQUENCE_LEN, 1)))
    model.add(Dropout(0.5))
    model.add(MaxPooling1D(3))
    model.add(Conv1D(100, 10, activation='relu'))
    model.add(Dropout(0.2))
    model.add(Conv1D(100, 10, activation='relu'))
    model.add(Dropout(0.2))
    model.add(Conv1D(10, 10, activation='relu'))
    model.add(GlobalAveragePooling1D())
    model.add(Dropout(0.5))
    model.add(Dense(1, activation = 'sigmoid'))
    print(model.summary())
    plot_model(model, to_file = 'figures/cnn_custom_diagram.png', 
               show_shapes = True, show_layer_names = True)
    return model

def create_wavenet_cnn_model():
    model = Sequential()
    model.add(Conv1D(1, 100, activation='relu', input_shape = (SEQUENCE_LEN, 1)))
    for rate in (1, 2, 4, 8) * 2:
        model.add(Conv1D(filters = 20, kernel_size = 2, padding = 'causal',
                         activation = 'relu', dilation_rate = rate))
        model.add(Dropout(0.1))
    model.add(Conv1D(filters = 10, kernel_size = 1))
    model.add(GlobalAveragePooling1D())
    model.add(Dense(1, activation = 'sigmoid'))
    print(model.summary())
    plot_model(model, to_file = 'figures/cnn_wavenet_diagram.png', 
               show_shapes = True, show_layer_names = True)
    return model

def train_cnn_model(model, model_name, x_train, y_train, x_val, y_val):
    chk = ModelCheckpoint('models/' + model_name + '.pkl', 
                          monitor = 'val_accuracy', 
                          save_best_only = True,
                          mode = 'max',
                          verbose = 1)
    cnn_history = LossHistory()
    callbacks_list = [
        chk,
        cnn_history
    ]
    
    model.compile(loss = 'binary_crossentropy',
                  optimizer = ADAM_CUSTOM, 
                  metrics = ['accuracy'])
    
    class_weights = create_class_weights(y_train)

    model.fit(x_train, y_train,
              class_weight = class_weights,
              batch_size = BS,
              epochs = EPOCHS,
              callbacks = callbacks_list,
              validation_data = (x_val, y_val))
    
    return model, cnn_history


# ------------------
# GRID SEARCH
# ------------------
def lstm_gridsearch(optimizer = 'adam'):
    model = Sequential()
    model.add(LSTM(256, input_shape = (SEQUENCE_LEN, 1), dropout = 0.2, recurrent_dropout = 0.2, return_sequences = True))
    model.add(LSTM(32, dropout = 0.2, recurrent_dropout = 0.2, return_sequences = True))
    model.add(LSTM(32, return_sequences = False))
    model.add(Dense(1, activation = 'sigmoid'))
    model.compile(loss = 'binary_crossentropy', optimizer = optimizer, metrics = ['accuracy'])
    return model  

def cnn_gridsearch(optimizer = 'adam'):
    model = Sequential()
    model.add(Conv1D(500, 100, activation='relu', input_shape = (SEQUENCE_LEN, 1)))
    model.add(Dropout(0.5))
    model.add(MaxPooling1D(3))
    model.add(Conv1D(100, 10, activation='relu'))
    model.add(Dropout(0.2))
    model.add(Conv1D(10, 10, activation='relu'))
    model.add(GlobalAveragePooling1D())
    model.add(Dropout(0.5))
    model.add(Dense(1, activation = 'sigmoid'))
    model.compile(loss = 'binary_crossentropy', optimizer = optimizer, metrics = ['accuracy'])
    return model

def build_gridsearch(build_function, x_train, y_train, model_name):
    grid = {'epochs': GS_EPOCHS,
            'batch_size': GS_BS,
            'optimizer': GS_OPTIMIZERS}
    load_model = KerasClassifier(build_fn = build_function)
    
    # train using GridSearch 
    validator = GridSearchCV(load_model,
                             param_grid = grid,
                             scoring = 'accuracy',
                             n_jobs = 1,
                             verbose = 10)
    validator.fit(x_train, y_train)
    
    # retain best model
    print('Best Parameters: ')
    model = validator.best_estimator_.model
    print(validator.best_params_)
    model.save('models/' + model_name + '.pkl')
    
    return model


# ------------------
# EVALUATE MODEL
# ------------------
def plot_batch_losses(history, plot_name):
    y1 = history.history['loss']
    y2 = history.history['acc']
    
    y3 = []
    for index, value in enumerate(y1):
        if value > 100:
            y3.append(index)
    for i in reversed(y3):
        del y1[i]
    
    x1 = np.arange(len(y1))
    fig, ax = plt.subplots()
    line1, = ax.plot(x1, y1, label = 'loss')
    ax.set_xlabel('Batch')
    ax.set_ylabel('Loss')
    plt.savefig('figures/' + plot_name + '.png')
    plt.show()
    
def model_acc(model_name):
    pickle_name = 'models/' + model_name + '.pkl'
    model = load_model(pickle_name)
    y_preds = model.predict_classes(x_test)
    acc = accuracy_score(y_test, y_preds)
    cm = confusion_matrix(y_test, y_preds)
    scores = precision_recall_fscore_support(y_test, y_preds, average = 'macro')
    return acc, cm, scores


# ------------------
# MAIN METHOD
# ------------------
if __name__=="__main__":
        
    # --------------------
    # DATA WRANGLING
    # --------------------
    # interictal
    data = get_data(PATH_INTERICTAL)
    timestamps = create_timestamps(data)
    data_filtered = iEEG_data_filter(data, FS, 0.16, 200, 60)
    fs_downSample = FS / DOWN_SAMPLE_FACTOR
    data_filtered_tmp = signal.decimate(data_filtered, DOWN_SAMPLE_FACTOR, axis = 0)
    data_filtered = pd.DataFrame(data_filtered_tmp, columns = data_filtered.columns); del data_filtered_tmp
    data_interictal = data_filtered
    
    # ictal
    data = get_data(PATH_ICTAL)
    timestamps = create_timestamps(data)
    data_filtered = iEEG_data_filter(data, FS, 0.16, 200, 60)
    fs_downSample = FS / DOWN_SAMPLE_FACTOR
    data_filtered_tmp = signal.decimate(data_filtered, DOWN_SAMPLE_FACTOR, axis = 0)
    data_filtered = pd.DataFrame(data_filtered_tmp, columns = data_filtered.columns); del data_filtered_tmp
    data_ictal = data_filtered
    
    # concatenate
    data = pd.concat([data_interictal, data_ictal], ignore_index = True)
    dataset, dataset_targets = create_merge_dataset(data, data_interictal.shape[0],
                                              START_TIME_INTERICTAL, END_TIME_INTERICTAL,
                                              START_TIME_ICTAL, END_TIME_ICTAL)
    
    # --------------------
    # DATASET PROCESSING
    # --------------------
    # optional preprocessing methods
    """ 
    dataset = scale_data(dataset)
    dataset = apply_pca(dataset, SEQUENCE_PCA)
    """ 
    
    # random split:
    """
    x_train, x_test, y_train, y_test = train_test_split(dataset, 
                                                        dataset_targets, 
                                                        test_size = 0.2)
    x_test, x_val, y_test, y_val = train_test_split(x_test, 
                                                    y_test, 
                                                    test_size = 0.5)
    print("Number of seizing instances in training set: " + 
          str(np.count_nonzero(y_train == 1)))
    print("Number of non-seizing instances in training set: " + 
          str(np.count_nonzero(y_train == 0)))
    print("Number of seizing instances in testing set: " + 
          str(np.count_nonzero(y_test == 1)))
    print("Number of non-seizing instances in testing set: " + 
          str(np.count_nonzero(y_test == 0)))
    """
    
    # per-electrode split:
    rows, cols, depth = dataset.shape
    num_train = int(TRAIN_SIZE * rows)
    num_test = int(TEST_SIZE * rows)
    num_val = int(VAL_SIZE * rows)
    
    x_train = dataset[ : num_train, :]
    x_test = dataset[num_train : num_train + num_test, :]
    x_val = dataset[num_train + num_test : , :]
    
    y_train = dataset_targets[ : num_train]
    y_test = dataset_targets[num_train : num_train + num_test]
    y_val = dataset_targets[num_train + num_test : ]
    print("Number of seizing instances in training set: " + 
          str(np.count_nonzero(y_train == 1)))
    print("Number of non-seizing instances in training set: " + 
          str(np.count_nonzero(y_train == 0)))
    print("Number of seizing instances in testing set: " + 
          str(np.count_nonzero(y_test == 1)))
    print("Number of non-seizing instances in testing set: " + 
          str(np.count_nonzero(y_test == 0)))
    
    
    # --------------------
    # OPTION 1: Models
    # --------------------
    # train wavenet CNN
    cnn_model = create_wavenet_cnn_model()
    cnn_model_name = 'eeg-model-cnn-wavenet'
    cnn_model, cnn_history = train_cnn_model(cnn_model, cnn_model_name, 
                                 x_train, y_train, x_val, y_val)
    plot_batch_losses(cnn_history, 'cnn-wavenet-history')
    
    # test wavenet CNN
    cnn_acc, cnn_cm, cnn_scores = model_acc(cnn_model_name)
    print("CNN WaveNet Test Set Accuracy: ")
    print("%.4f" % round(cnn_acc, 4))   
    print("CNN WaveNet Test Set Confusion Matrix: ")
    print(cnn_cm)
    
    
    """
    # train LSTM
    lstm_model = create_lstm_model()
    lstm_model_name = 'eeg-model-lstm'
    lstm_model, lstm_history = train_lstm_model(lstm_model, lstm_model_name, 
                                  x_train, y_train, x_val, y_val)
    plot_batch_losses(lstm_history, 'lstm-history')
    
    # test LSTM
    lstm_acc, lstm_cm, lstm_scores = model_acc(lstm_model_name)
    print("LSTM Test Set Accuracy: ")
    print("%.4f" % round(lstm_acc, 4))
    print("LSTM Test Set Confusion Matrix: ")
    print(lstm_cm)
    
    
    # train custom CNN
    cnn_model = create_custom_cnn_model()
    cnn_model_name = 'eeg-model-cnn'
    cnn_model, cnn_history = train_cnn_model(cnn_model, cnn_model_name, 
                                 x_train, y_train, x_val, y_val)
    plot_batch_losses(cnn_history, 'cnn-history')
    
    # test CNN
    cnn_acc, cnn_cm, cnn_scores = model_acc(cnn_model_name)
    print("CNN Test Set Accuracy: ")
    print("%.4f" % round(cnn_acc, 4))  
    print("CNN Test Set Confusion Matrix: ")
    print(cnn_cm)
    """
    
    # --------------------
    # OPTION 2: GridSearch
    # --------------------
    """
    # train and test LSTM
    gs_lstm_name = 'egg-lstm-gridsearch'
    gs_lstm = build_gridsearch(lstm_gridsearch, x_train, y_train, gs_lstm_name)
    gs_lstm_acc = model_acc(gs_lstm_name)
    print("GridSearch LSTM Test Set Accuracy: ")
    print("%.4f" % round(gs_lstm_acc, 4))
    
    # train and test CNN
    gs_cnn_name = 'eeg-cnn-gridsearch'
    gs_cnn = build_gridsearch(cnn_gridsearch, x_train, y_train, gs_cnn_name)
    gs_cnn_acc = model_acc(gs_cnn_name)
    print("GridSearch CNN Test Set Accuracy: ")
    print("%.4f" % round(gs_cnn_acc, 4))
    """
    
    