#Importing libraries
import torch
import torch.nn as nn
from torchvision import transforms, datasets, models
import numpy as np
import matplotlib.pyplot as plt
import torch.nn.functional as F
from torch.utils.data.sampler import SubsetRandomSampler
from torch.utils.tensorboard import SummaryWriter
import os 
import glob 
import re 
import time
import csv
import errno, shutil
from typing import Union, Optional, Type, Any, List, Callable


Model_names = ['resnet18', 'resnet50', 'resnet101', 'resnet152']

class ModelNotFoundError(RuntimeError):
    """
    A class for custom Error. Raises ModelNotFoundError if
    the model name entered does not belong to supported models.

    """
    def __init__(self, arg):
        self.args = arg

class DataNotArrangedError(RuntimeError):
    """
    A class for custom Error. Raises DataNotArrangedError if
    the Data is not arranged in a manner required for torchvision's
    ImageFolder Moduel.

    """
    def __init__(self, arg):
        self.args = arg

class FeaturesNotProvidedError(RuntimeError):
    """
    A class for custom Error. Raises FeaturesNotProvidedError if
    the user specifies number of FC layers more than one but does 
    not give the number of hidden units for that extra layer.

    """
    def __init__(self, arg):
        self.args = arg



class Classifier():

    def __init__(self, exp_no: int, model_to_train: str, dataset_dir: str, 
                performance_dir: str, gpu: Optional[bool] = None):

        """
        Initialization of a class Classifier.

        Args:
        exp_no (int): Current experiment number so as to locate the current 
                    experiment's file.
        model_to_train (str): name of the pretrained model to train
        dataset directory (str): Directory containing the data
        performance directory (str): The directory where the generated text, checkpoints
                                    model_stats will be saved.
        gpu (bool): Boolean indicating availability of a GPU

        Returns:
        None.

        """
                    
        self.exp_no = exp_no

        if model_to_train not in Model_names:
            raise ModelNotFoundError("No such model: " + model_to_train)
            

        if not os.path.isdir(performance_dir):
            raise FileNotFoundError(errno.ENOENT, 
                                    os.strerror(errno.ENOENT), performance_dir)
            

        if not os.path.isdir(dataset_dir):
            raise FileNotFoundError(errno.ENOENT, 
                                    os.strerror(errno.ENOENT), dataset_dir)
            

        if gpu is not None:
            self.device = 'cuda'
        else:
            self.device = 'cpu'

        self.Model_name = model_to_train
        self.Dataset_dir = dataset_dir
        self.Performance_dir = performance_dir
        
        self.write_data(['##############################################', 
                        '\tModel Data for experiment: ' + str(self.exp_no), 
                        '\tModel Trained: ' + model_to_train, 
                        '\tData set used: ' + os.path.basename(self.Dataset_dir), 
                        '##############################################'], 
                        self.exp_no, continuing=True)



    def load_data(self, batch_size: int = 128, valid_size: float = 0.2, 
                num_workers: int = 1) -> None:

        """
        A Method for Loading the data from the given dataset directory.

        Args:
        batch_size (int): Batch size for loading images
        valid_size (float): Percentage of the training data to be used for validation
        num_workers (int): Specified for parallel loading of the data using an idle CPU core

        Returns:
        None.

        """

        # Defining means and standard deviations for RGB channels as 
        # required by Pre-trained models
        means = [0.485, 0.456, 0.406]
        stdevs = [0.229, 0.224, 0.225]

        
        # Defining transforms
        train_transform = transforms.Compose([transforms.Resize(255), 
                                            transforms.RandomResizedCrop(224), 
                                            transforms.ToTensor(), 
                                            transforms.Normalize(means, stdevs)])

        test_transform = transforms.Compose([transforms.Resize(255), 
                                            transforms.CenterCrop(224),
                                            transforms.ToTensor(),
                                            transforms.Normalize(means, stdevs)])

        
        # Loading data
        if not os.path.isdir(self.Dataset_dir + '/train'):
            self.delete_file(self.exp_no, stats_delete=False)
            raise DataNotArrangedError("Data not arranged properly in folders. \
                                        Expected similar to  ../train/xyz.png")
            

        if not os.path.isdir(self.Dataset_dir + '/test'):
            self.delete_file(self.exp_no, stats_delete=False)
            raise DataNotArrangedError("Data not arranged properly in folders. \
                                        Expected similar to  ../test/xyz.png")
            

        train_data = datasets.ImageFolder(self.Dataset_dir + '/train', 
                                        transform=train_transform)
        test_data = datasets.ImageFolder(self.Dataset_dir + '/test', 
                                        transform=test_transform)

        
        # Automatically listing the Classes
        self.classes = list(train_data.class_to_idx.keys())


        # For shuffling training examples and allocating validation set
        indices = np.arange(len(train_data))
        np.random.shuffle(indices)
        split = int(np.floor(valid_size * len(train_data)))
        train_idx, valid_idx = indices[split:], indices[:split]


        # Defining Samplers
        train_sampler = SubsetRandomSampler(train_idx)
        valid_sampler = SubsetRandomSampler(valid_idx)


        self.trainloader = torch.utils.data.DataLoader(train_data, 
                                                    batch_size=batch_size,
                                                    sampler=train_sampler, 
                                                    num_workers=num_workers)
        self.validloader = torch.utils.data.DataLoader(train_data, 
                                                    batch_size=batch_size, 
                                                    sampler=valid_sampler, 
                                                    num_workers=num_workers)
        self.testloader = torch.utils.data.DataLoader(test_data, 
                                                    batch_size=batch_size, 
                                                    num_workers=num_workers)

        self.write_data(["Number of Training Examples: " + \
                        str(len(self.trainloader.sampler)), 
                        "Number of Testing Examples: " + \
                        str(len(self.testloader.sampler)), 
                        "Number of classes: " + \
                        str(len(self.classes))], 
                        self.exp_no, continuing=True)



    def load_model(self, freeze_conv_layers: Optional[bool] = None, 
                num_FCL: Optional[int] = 1, 
                features: Optional[List] = None) -> None:
        """
        A Method for imporing the model from the torchvision's 
        pretrained models.

        Args:
        freeze_conv_layers (bool): A boolean specifying whether to train all the layers or 
                                    freeze them and modify only last FC layers
        num_FCL (int): Number of fully connected layers attached to the end of the 
                        convolutional blocks
        features (list): If number of fully connected layers in more than 1, features list gives 
                    the number of hidden units desired in the extra fully connected layers.

        Returns:
        None.

        """

        if self.Model_name == 'resnet18':
            self.TempNetwork = models.resnet18(pretrained=True)
        elif self.Model_name == 'resnet50':
            self.TempNetwork = models.resnet50(pretrained=True)
        elif self.Model_name == 'resnet101':
            self.TempNetwork = models.vgg16(pretrained=True)
        elif self.Model_name == 'resnet152':
            self.TempNetwork = models.resnet101(pretrained=True)


        # To prevent the convolutional layers from training
        if freeze_conv_layers:
            for param in self.TempNetwork.parameters():
                param.requires_grad = False

        self.write_data('Number of Fully connected layers: ' + str(num_FCL), self.exp_no)


        # Extracting the number of features of the last layer
        in_features = list(enumerate(self.TempNetwork.modules()))[-1][1].in_features


        # This is for removing the last Fully connected layers
        i = 0
        self.Network = nn.Sequential()
        for name, child in self.TempNetwork.named_children():
            i += 1
            if i == len(list(self.TempNetwork.named_children())):
                break
            self.Network.add_module(name, child)
        
        self.Network.flatten = nn.Flatten()

        # Now adding custom FC layers    
        if num_FCL != 1:

            if features is None:
                self.delete_file(self.exp_no, stats_delete=False)
                raise FeaturesNotProvidedError("List of Number of features in FC \
                                                layers expected at input. Got none.")
            
            self.Network.fc = nn.Sequential()
            for num in range(num_FCL - 1):
                
                name = 'fc' + str(num + 1)
                child = nn.Sequential(nn.Linear(in_features, 
                                                features[num]), 
                                                nn.ReLU())
                
                self.Network.fc.add_module(name, child)
                in_features = features[num]

            name = 'fc' + str(num_FCL)
            child = nn.Linear(features[-1], len(self.classes))
            self.Network.fc.add_module(name, child)
        
        else:
            self.Network.fc = nn.Linear(in_features, len(self.classes))

        self.Network.to(self.device)



    def setup_optimization(self, 
                        optimizer_name: str = 'SGD', 
                        scheduler_name: str = 'reduceOnPlateau', 
                        learning_rate: float = 0.01, 
                        factor: float = 0.1, 
                        threshold: float = 0.0001, 
                        patience: int = 5, 
                        step_size: int = 30, 
                        gamma: float = 0.1) -> None:

        """
        A Method for setting up the optimization parameters like learning rate. Also 
        sets up Learning rate scheduler parameters like factor, threshold, patience etc.

        Args:
        optimizer (str): The name of the optimizer. Choose between ('adam', 'SGD')
        scheduler (str): The name of the scheduler. Choose between ('StepLR', 'reduceOnPlateau')
        learning_rate (float): Starting learning rate to use (will be changed by lR scheduler 
                            as required)
        factor (float): A factor to multiply learning_rate with so as to reduce it after validation
                        loss stops decreasing.
        threshold (float): Indicates the threshold for difference between previous epoch
                        validation loss and the current below which the factor will be multiplied
                        to the learning_rate
        patience (int): number of epochs to wait before reducing the learning rate. (Sometimes
                        the validation loss will decrease even after increasing for a prev epoch)
        step_size (int): number of epochs after which the learning rate will be multiplied by 
                        gamma. (Used only for StepLR scheduler)
        gamma (float): Factor to multiply learning_rate with so as to reduce it after 'step_size' 
                        number of epochs.

        Returns:
        None.

        """

        # Saving Learning rate. This is the initial learning rate
        self.lr = learning_rate
        self.write_data(['Optimizer name: ' + optimizer_name, 
                        'Scheduler name: ' + scheduler_name, 
                        'Starting Learning rate: ' + str(learning_rate)], 
                        self.exp_no, continuing=True)


        if optimizer_name not in ['adam', 'SGD']:
            self.delete_file(self.exp_no, stats_delete=False)
            raise IOError("Support only for Adam and SGD for now! Found: " + optimizer_name)


        if scheduler_name not in ['reduceOnPlateau', 'StepLR']:
            self.delete_file(self.exp_no, stats_delete=False)
            raise IOError("Support only for reduceOnPlateau and StepLR \
                        for now! Found: " + scheduler_name)


        # Setting up desired optimizer
        if optimizer_name == 'adam':
            self.optimizer = torch.optim.Adam(self.Network.fc.parameters(), lr=learning_rate)

        self.optimizer = torch.optim.SGD(self.Network.fc.parameters(), lr=learning_rate)
        
            
        # Setting up desired Scheduler
        if scheduler_name == 'StepLR':
            self.scheduler = torch.optim.lr_scheduler.StepLR(self.optimizer, 
                                                            step_size=step_size, 
                                                            gamma=gamma, 
                                                            verbose=True)
            
            self.write_data(['StepLR scheduler Parameters:-', 
                            'Step size: ' + str(step_size), 
                            'Gamma: ' + str(gamma)], 
                            self.exp_no, continuing=True)
        

        self.scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(self.optimizer, 
                                                                    factor=factor, 
                                                                    patience=patience, 
                                                                    threshold=threshold, 
                                                                    verbose=True)
        self.write_data(['reduceOnPlateau Scheduler parameters:-', 
                        'Factor: ' + str(factor), 
                        'Threshold: ' + str(threshold), 
                        'Patience: ' + str(patience)], 
                        self.exp_no, continuing=True)

        
        # Defining loss function
        self.criterion = nn.CrossEntropyLoss()

        

    def train(self, 
            n_epochs: int = 30, 
            custom_path: Optional[str] = None, 
            load_prev: Optional[str] = None, 
            valid_loss: Optional[float] = None) -> None:
        
        """
        A method for beginning Training the model. Either starts training from beginning,
        or from a pervious checkpoint or from a custom checkpoint.

        Args:
        n_epochs (int): Number of epochs to train the model
        custom_path (str): Path of the custom checkpoint desired to be loaded. 
                        The hyperparameters for this checkpoint will be read from 
                        the correspoiding Text file.
        load_prev (str): Path of the checkpoint desired to be loaded. The hyperparameters 
                        for this checkpoint will be read from the correspoiding Text file.
        valid_loss (float): Minimum valid loss that was attained during previous training. 
                        Required for comparison with the valid loss of the new experiment's 
                        first epoch so as to decide whether there was an improvement.

        Returns:
        None.

        """

        # Creating directories
        model_stats_dir = self.Performance_dir + '\\Model_Stats\\exp_' + str(self.exp_no)
        checkpoint_dir = self.Performance_dir + '\\Checkpoints'
        
        if not os.path.isdir(checkpoint_dir):
            os.makedirs(checkpoint_dir)

        
        Net = self.Network


        # Creating Checkpoint path .pth for saving
        name = self.Model_name
        checkpoint_name = name + '_' + str(self.exp_no) + '.pth'
        checkpoint_path = checkpoint_dir + '\\' + checkpoint_name
        

        if custom_path is not None:

            Net.load_state_dict(torch.load(custom_path))
            if valid_loss is None:
                self.delete_file(self.exp_no, stats_delete=False)
                raise IOError("Expected Valid loss input when loading from custom path. Got None")
            
            # Valid loss for comparison so as to save model for lowest valid loss
            valid_loss_min = valid_loss
            print(f'{custom_path} loaded. \nTraining from custom checkpoint...')
            self.write_data('Model Loaded from Custom path: ' + custom_path, self.exp_no)


        elif load_prev is not None:

            Net.load_state_dict(torch.load(load_prev))
            valid_loss_min = valid_loss
            self.write_data('Model Loaded from previous checkpoint: ' + load_prev, self.exp_no)
        
        else:
            valid_loss_min = np.Inf
            print('Training from Beginning.')
            self.write_data('Trained From beginning', self.exp_no)



        if not os.path.isdir(model_stats_dir):
            os.makedirs(model_stats_dir)
        
        writer = SummaryWriter(model_stats_dir)
        
        counter = 0
        

        self.train_losses, self.valid_losses = [], []
        glob_iters = 0

        improvements = 0
        try:
            for epoch in range(1, n_epochs+1):
                
                #Keep track of train and validation losses
                train_loss = 0.0
                valid_loss = 0.0
                num_iters = 0
                writer.add_scalar('Learning rate', self.optimizer.param_groups[0]['lr'])
                print('Learning Rate:', self.optimizer.param_groups[0]['lr'])
                


                ################################
                ###### Training the model ######
                ################################
                tic_train = time.time()
                Net.train()
                for data, target in self.trainloader:
                    num_iters += 1
                    glob_iters += 1
                    data, target = data.to(self.device), target.to(self.device)
                    #print(data.shape)
                    #Clear the gradients of all optimized variables 
                    # (They remain in memory. We need new gradients)
                    self.optimizer.zero_grad()
                    output = Net(data)

                    #Calculating the batch loss
                    loss = self.criterion(output, target)

                    #Backpropagate throught the network
                    loss.backward()

                    #Perform a single optimization step 
                    #update all the parameters based on the batch just processed
                    self.optimizer.step()

                    #Updating the training loss
                    train_loss += loss.item()*data.size(0)
                
                    #Writing to Summary
                    writer.add_scalar('Training loss', 
                                    train_loss/(num_iters*data.shape[0]), 
                                    global_step=glob_iters)
                
                toc_train = time.time()
                print(f'Training time taken: {round((toc_train - tic_train)/60)} Minutes')
                
                
                
                
                ##################################
                ###### Evaluating the model ######
                ##################################
                
                tic_val = time.time()
                Net.eval()
                for data, target in self.validloader:

                    data, target = data.to(self.device), target.to(self.device)

                    #forward pass to compute predictions for validation batch
                    output = Net(data)

                    #Calculate the batch loss
                    loss = self.criterion(output, target)

                    #Update validation loss
                    valid_loss += loss.item()*data.size(0)
                
                #Writing to Summary
                writer.add_scalar('Validation loss', 
                                valid_loss/len(self.validloader.sampler), global_step=glob_iters)
                
                toc_val = time.time()
                print(f'Validation time taken: {round((toc_val - tic_val)/60)} Minutes')
                
                
                #Updating train and validation loss
                train_loss = train_loss/len(self.trainloader.sampler)
                valid_loss = valid_loss/len(self.validloader.sampler)
                self.train_losses.append(train_loss)
                self.valid_losses.append(valid_loss)

                #Update the learning rate after plateau in valid loss
                self.scheduler.step(valid_loss)
                
                print(f'Epoch: {epoch} \tTraining Loss: \
                    {train_loss:.6f} \tValidation Loss: {valid_loss:.6f}')

                # Processes if the valid loss is less than the least validation loss seen
                
                if valid_loss <= valid_loss_min:
                    
                    
                    print(f'Validation loss decreased \
                        ({valid_loss_min:.6f} ---> {valid_loss:.6f}) \tSaving Model...')
                    torch.save(Net.state_dict(), checkpoint_path)
                    if improvements != 0:
                        replace = True 
                    else:
                        replace = False
                    self.write_data('Previous Min Valid loss: ' + \
                        str(valid_loss_min), self.exp_no, continuing=True, replace=replace)
                    self.write_data('Minimum Valid loss: ' + \
                        str(valid_loss), self.exp_no, replace=replace)
                    self.write_data('Best Learning rate that gave min valid loss: ' + \
                        str(self.optimizer.param_groups[0]['lr']), self.exp_no, replace=replace)
                    valid_loss_min = valid_loss
                    improvements += 1

                
                #Print Time remaining
                counter += 1
                epoch_time = ((toc_train - tic_train) + (toc_val - tic_val))/60
                print(f'Epoch {epoch} complete.')
                print(f'Estimated Time left: {round(epoch_time*(n_epochs-counter))} Minutes\n')


            if improvements == 0:
                print('No improvements')
                self.delete_file(self.exp_no)

            if improvements != 0:
                ################################
                ######## Testing Model #########
                ################################

                test_loss = 0.0
                class_correct = [0. for i in range(len(self.classes))]
                class_correct5 = [0. for i in range(len(self.classes))]
                class_total = [0. for i in range(len(self.classes))]

                Net.eval()
                print('Now Testing....')
                
                #Iterate over test data
                for data, target in self.testloader:

                    data, target = data.to(self.device), target.to(self.device)
                    
                    # Forward pass through the network to compute predictions
                    output = Net(data)
                    
                    #Calculate loss
                    loss = self.criterion(output, target)
                        
                    #Update test loss
                    test_loss += loss.item()*data.size(0)
                    
                    # Convert output probabilities to predicted class
                    _, pred = torch.max(output, 1)
                    _, pred5 = output.topk(5, dim=1)

                    #Compare with the target labels
                    correct_tensor = pred.eq(target.data.view_as(pred))

                    target5 = target.data.view(pred.shape[0], 1)
                    correct_tensor5 = torch.eq(pred5, target5)
                    correct = np.squeeze(correct_tensor.numpy()) \
                                        if not self.device=='cuda' \
                                        else np.squeeze(correct_tensor.cpu().numpy())
                    correct5 = np.squeeze(correct_tensor5.numpy()) \
                                        if not self.device=='cuda' \
                                        else np.squeeze(correct_tensor5.cpu().numpy())
                    
                    #For calculating test accuracy of each class
                    for i in range(len(data)):
                        label = target[i].item()
                        class_correct[label] += correct[i].item()
                        class_correct5[label] += np.sum(correct5, axis=1, keepdims=True)[i].item()
                        class_total[label] += 1
                            
                test_loss = test_loss/len(self.testloader.dataset)
                print(f'Test loss: {test_loss:.6f}')
                self.write_data('Testing loss: ' + str(test_loss), self.exp_no)

                for i in range(len(self.classes)):
                    if class_total[i] > 0:
                        print(f'Top-1 Test accuracy of {self.classes[i]}: \
                            {(100*(class_correct[i]/class_total[i])):.3f}         \
                            {(class_correct[i])}/{class_total[i]}')
                        
                        self.write_data('Top-1 Accuracy of class ' + self.classes[i] + ' : ' + \
                                        str((100*(class_correct[i]/class_total[i]))), 
                                        self.exp_no, continuing=True)
                        
                        print(f'Top-5 Test accuracy of {self.classes[i]}: \
                            {(100*(class_correct5[i]/class_total[i])):.3f}         \
                            {(class_correct5[i])}/{class_total[i]}')
                        
                        self.write_data('Top-5 Accuracy of class ' + self.classes[i] + ' : ' + \
                                        str((100*(class_correct5[i]/class_total[i]))), 
                                        self.exp_no)

                    else:
                        print(f'Test accuracy of {self.classes[i]}: NA (no training examples)')
                        
                print(f'\nTop-1 Test Accuracy Overall: \
                    {(100*(np.sum(class_correct)/np.sum(class_total))):.2f} \
                    {np.sum(class_correct)}/{np.sum(class_total)}')
                
                print(f'\nTop-5 Test Accuracy Overall: \
                    {(100*(np.sum(class_correct5)/np.sum(class_total))):.2f} \
                    {np.sum(class_correct5)}/{np.sum(class_total)}')
                
                accuracy = 100*(np.sum(class_correct)/np.sum(class_total))
                accuracy5 = 100*(np.sum(class_correct5)/np.sum(class_total))
                self.write_data(['Overall Top-1 accuracy : ' + str(accuracy), 
                                'Overall Top-5 accuracy: ' + str(accuracy5)], self.exp_no)

                
            writer.close()



        except KeyboardInterrupt:
            print('Training interrupted')
            writer.close()
            
            decision = input('Do you want to save current state? y/n\n>>>>')
            if decision == 'y':
                torch.save(Net.state_dict(), checkpoint_path)
                self.write_data('Training interrupted.', self.exp_no)
                print('Saving Model...')
            else:
                print('Current state not saved.')
                self.delete_file(self.exp_no)



    def write_data(self, lines: Union[str, List], exp_no: int, 
                continuing: bool = False, replace: bool = False):

        """
        A method for writing the data to a text file. The method is called
        at places where the data needs to be written to the file.

        Args:
        lines Union[str, List]: Line of lines to be written to the text file
        exp_no (int): Current experiment number so as to locate the current 
                    experiment's file.
        continuing (bool): A boolean specifying whether to add the dashed lines 
                        for easy organization. Should be False if the data to be 
                        written belongs to a particular category.
        replace (bool): A boolean specifying whether to replace the older version
                        of a line with a newer version that is automatically detected.
                        Used for replacing the Minimum valid loss entries once a better
                        valid loss is reached.
        
        Returns:
        None.

        """

        Experiment_info_dir = self.Performance_dir + '\\Exp_data'
        if not os.path.isdir(Experiment_info_dir):
            os.makedirs(Experiment_info_dir)
        filename = 'Test Report_' + str(exp_no) + '.txt'

        if not replace:
            with open(Experiment_info_dir + '\\' + filename, "a+") as tfile:
                if isinstance(lines, list):
                    for line in lines:
                        tfile.write(line + '\n')
                        if continuing:
                            pass
                        else:
                            tfile.write('\n----------------------------------------------\n\n')
                    if continuing:
                        tfile.write('\n----------------------------------------------\n\n')
                else:
                    tfile.write(lines + '\n')
                    if continuing:
                            pass
                    else:
                        tfile.write('\n----------------------------------------------\n\n')
        else:
            old_str_identifier = (re.findall('.{15}', lines))[0]
            t = open(Experiment_info_dir + '\\' + filename, 'rt')
            data = t.read()
            old_string = re.findall(old_str_identifier + '.+', data)[0]
            data = data.replace(old_string, lines)
            t.close()
            tw = open(Experiment_info_dir + '\\' + filename, 'wt')
            tw.write(data)
            tw.close()



    def delete_file(self, exp_no: int, stats_delete=True):

        """
        A method to delete the generated files if the program runs into an error.

        Args:
        exp_no (int): Current experiment number so as to locate the current 
                    experiment's file.
        stats_delete (bool): A boolean specifying whether to delete the stats file
                            generated by tensorboard summarywriter.
        
        Returns:
        None.

        """

        Experiment_info_dir = self.Performance_dir + '\\Exp_data'
        model_stats_dir = self.Performance_dir + '\\Model_Stats\\exp_' + str(exp_no)
        if stats_delete:
            try:
                shutil.rmtree(model_stats_dir)
            except OSError as e:
                print("Error: %s : %s" % (model_stats_dir, e.strerror))
        
        if glob.glob(Experiment_info_dir + '\\*.txt') == []:
            raise FileNotFoundError("File to delete not found!")

        for file in glob.glob(Experiment_info_dir + '\\*.txt'):
            if re.search('_' + str(exp_no), file):
                os.remove(file)
        

    def un_normalize(self, img):

        """
        A method to perform reverse normalization so as to plot the image
        for visualization and presentation.

        Args:
        img (torch.Tensor): Image upon which to perform reverse normalization

        Returns:
        None.

        """
            
        means = [0.485, 0.456, 0.406]
        stdevs = [0.229, 0.224, 0.225]
        
        means, stdevs = np.array(means), np.array(stdevs)

        # Reversing Normalization that was done previously
        means = list(means*-1)
        stdevs = list(1/stdevs)

        transform = transforms.Normalize(means, stdevs)
        img = transform(img)
        return img



    def visualize_histogram(self):

        """
        A method for creating a figure which consists of an image and the
        predicted class is the form of a histogram.

        Args:
        None.

        Returns:
        None.
        
        """

        dataiter = iter(self.testloader)
        i = 0
        
        while True:
            
            i += 1
            
            images, _ = dataiter.next()
            image = images[0].view(1, 3, 224, 224)
            images.numpy()
            image_to_view = self.un_normalize(images[0])


            image = image.to(self.device)

            output = self.Network(image)
            ps = F.softmax(output, dim=1)

            self.view_classify(i, image_to_view, ps)
            user_prompt = input('Hit Enter to load next image! Press q to quit')
            if user_prompt == 'q':
                break



    def view_classify(self, i, img, ps):
        
        """
        A method that assists 'classification_histogram()'
         for viewing an image and it's predicted classes.
         
         Args:
         i (int): Image number
         img (torch.Tensor): image to use in figure
         ps (torch.Tensor): probability of image class
         
         Returns:
         None.
         
         """

        if self.device == 'cuda':
            ps = ps.data.cpu().numpy().squeeze()
        else:
            ps = ps.data.numpy().squeeze()

        fig, (ax1, ax2) = plt.subplots(figsize=(6,9), ncols=2)
        img = np.transpose(img, (1, 2, 0))
        ax1.imshow(img)
        ax1.axis('off')
        ax2.barh(np.arange(len(self.classes)), ps)
        ax2.set_aspect(0.1)
        ax2.set_yticks(np.arange(len(self.classes)))
        ax2.set_yticklabels(self.classes, size='small');
        ax2.set_title('Class Probability')
        ax2.set_xlim(0, 1.1)

        plt.tight_layout()
        name = 'hist' + str(i) + '.png'
        path_name = self.Performance_dir + '\\histogram'

        if not os.path.isdir(path_name):
            os.makedirs(path_name)
        plt.savefig(path_name + '\\' + name)
        plt.show()



    def imshow(self, img):

        """
        A method to show image after performing reverse normalization.

        Args:
        img (torch.Tensor): Image on which to be performed reverse normalization

        Returns:
        None.

        """
        
        img = self.un_normalize(img)

        # In Pytorch, dimensions are (C, W, H) but in matplotlib - (W, H, C)
        plt.imshow(np.transpose(img, (1, 2, 0)))



    def plot_image_class(self, subplot_rows: int = 5, 
                        subplot_cols: int = 4):
        
        """
        A method for creating a figure in which images will be plotted in subplots
        with their title representing the class that image belongs to.

        Args:
        subplot_rows (int): Number of rows of images in subplots
        subplot_cols (int): Number of columns of images in subplots

        Returns:
        None.
        
        """

        figWidth = subplot_cols*4
        figHeight = subplot_rows*4
        
        dataiter = iter(self.testloader)
        images, labels = dataiter.next()

        #Want only num_images for visualization. No need of full batch
        num_images = subplot_cols*subplot_rows
        images = images[:num_images]
        images = images.view(num_images, 3, 224, 224)


        images, labels = images.to(self.device), labels.to(self.device)
            
        output = self.Network(images)

        _, preds_tensor = torch.max(output, 1)
        preds = np.squeeze(preds_tensor.numpy()) if not self.device == 'cuda' else np.squeeze(preds_tensor.cpu().numpy())

        fig = plt.figure(figsize=(figWidth, figHeight))
        for idx in range(num_images):
            ax = fig.add_subplot(subplot_rows, subplot_cols, idx+1, xticks=[], yticks=[])
            self.imshow(images.cpu()[idx])
            ax.set_title("{} ({})".format(self.classes[preds[idx]], self.classes[labels[idx]]),
                        color=("green" if preds[idx] == labels[idx].item() else "red"), 
                        fontsize=11)
        
        
        img_name = 'image_class' + str(self.exp_no) + '.png'        
        path_name = self.Performance_dir + '\\Image_and_class'

        if not os.path.isdir(path_name):
            os.makedirs(path_name)
        plt.savefig(path_name + '\\' + img_name)
        plt.show()



    def save_model(self, save=False):

        """
        A method to save the whole model to be used for inference ahead.

        Args:
        save (bool): A boolean specifying whether to save the whole model

        Returns:
        None.

        """

        if save:
            path_name = self.Performance_dir + '\\' + 'whole_model'
            if not os.path.isdir(path_name):
                os.makedirs(path_name)
            torch.save(self.Network, path_name + '\\Complete_model_' + str(self.exp_no) + '.pth')