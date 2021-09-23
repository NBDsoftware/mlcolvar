"""Linear discriminant analysis-based CVs."""

import torch
import numpy as np
from .models import NeuralNetworkCV
from torch.utils.data import Dataset, DataLoader

class LinearDiscriminant:
    """
    Linear Discriminant CV

    Attributes
    ----------
    d_ : int
        Number of classes
    evals_ : torch.Tensor
        LDA eigenvalues
    evecs_ : torch.Tensor
        LDA eignvectors
    S_b_ : torch.Tensor
        Between scatter matrix
    S_w_ : torch.Tensor
        Within scatter matrix
        
    Methods
    -------
    fit(x,label)
        Fit LDA given data and classes
    transform(x)
        Project data to maximize class separation
    fit_transform(x,label)
        Fit LDA and project data 
    get_params()
        Return saved parameters
    set_features_names(names)
        Set features names
    plumed_input()
        Generate PLUMED input file
    """

    def __init__(self):
        super().__init__()
        # initialize attributes
        self.evals_ = None
        self.evecs_ = None
        self.S_b_ = None
        self.S_w_ = None
        self.d_ = None
        self.features_names_ = None

        # Regularization
        self.sw_reg = 1e-6

        # Initialize device and dtype
        self.dtype_ = torch.float32
        self.device_ = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    def LDA(self, H, label, save_params = True):
        """
        Internal method which performs LDA and saves parameters.
        
        Parameters
        ----------
        H : array-like of shape (n_samples, n_features)
            Training data.
        label : array-like of shape (n_samples,) 
            Classes labels. 
        save_params: bool, optional
            Whether to store parameters in model
            
        Returns
        -------
        evals : array of shape (n_classes-1)
            LDA eigenvalues.
        """
        
        #sizes
        N, d = H.shape
        
        #classes
        categ = torch.unique(label)
        n_categ = len(categ)
        self.d_ = d
        
        # Mean centered observations for entire population
        H_bar = H - torch.mean(H, 0, True)
        #Total scatter matrix (cov matrix over all observations)
        S_t = H_bar.t().matmul(H_bar) / (N - 1)
        #Define within scatter matrix and compute it
        S_w = torch.Tensor().new_zeros((d, d), device = self.device_, dtype = self.dtype_)    
        S_w_inv = torch.Tensor().new_zeros((d, d), device = self.device_, dtype = self.dtype_)
        #Loop over classes to compute means and covs
        for i in categ:
            #check which elements belong to class i
            H_i = H[torch.nonzero(label == i).view(-1)]
            # compute mean centered obs of class i
            H_i_bar = H_i - torch.mean(H_i, 0, True)
            # count number of elements
            N_i = H_i.shape[0]
            if N_i == 0:
                continue
            S_w += H_i_bar.t().matmul(H_i_bar) / ((N_i - 1) * n_categ)       

        S_b = S_t - S_w

        # Regularize S_w
        S_w = S_w + self.sw_reg * torch.diag(torch.Tensor().new_ones((d), device = self.device_, dtype = self.dtype_))

        # -- Generalized eigenvalue problem: S_b * v_i = lambda_i * Sw * v_i --

        # (1) use cholesky decomposition for S_w
        L = torch.cholesky(S_w,upper=False)

        # (2) define new matrix using cholesky decomposition
        L_t = torch.t(L)
        L_ti = torch.inverse(L_t)
        L_i = torch.inverse(L)
        S_new = torch.matmul(torch.matmul(L_i,S_b),L_ti)

        #(3) find eigenvalues and vectors of S_new
        eigvals, eigvecs = torch.symeig(S_new,eigenvectors=True)
        #sort
        eigvals, indices = torch.sort(eigvals, 0, descending=True)
        eigvecs = eigvecs[:,indices]
        
        #(4) return to original eigenvectors
        eigvecs = torch.matmul(L_ti,eigvecs)

        #normalize them
        for i in range(eigvecs.shape[1]): # TODO maybe change in sum along axis?
            norm=eigvecs[:,i].pow(2).sum().sqrt()
            eigvecs[:,i].div_(norm)
        #set the first component positive
        eigvecs.mul_( torch.sign(eigvecs[0,:]).unsqueeze(0).expand_as(eigvecs) )
        
        #keep only C-1 eigvals and eigvecs
        eigvals = eigvals[:n_categ-1]
        eigvecs = eigvecs[:,:n_categ-1]#.reshape(eigvecs.shape[0],n_categ-1)

        if save_params:
            self.evals_ = eigvals
            self.evecs_ = eigvecs
            self.S_b_ = S_b
            self.S_w_ = S_w
        
        return eigvals

    def fit(self,X,y):
        """
        Fit LDA given data and classes.
        
        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Training data.
        y : array-like of shape (n_samples,) 
            Classes labels. 
        """
        
        if type(X) != torch.Tensor:
            X = torch.tensor(X,dtype=self.dtype_,device=self.device_)
        if type(y) != torch.Tensor:
            y = torch.tensor(y,device=self.device_) #class labels are integers
        _ = self.LDA(X,y)
    
    def transform(self,X):
        """
        Project data to maximize class separation.
        
        Parameters
        ----------
        X : array-like of shape (n_samples, n_features) or (n_features)
            Inference data.
               
        Returns
        -------
        s : array-like of shape (n_samples, n_classes-1)
            LDA projections.
        """
        if type(X) != torch.Tensor:
            X = torch.tensor(X,dtype=self.dtype_,device=self.device_)
            
        s = torch.matmul(X,self.evecs_)
    
        return s
        
    def fit_transform(self,X,y):
        """
        Fit LDA and project data.
        
        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Training data.
        y : array-like of shape (n_samples,) 
            Classes labels. 
        
        Returns
        -------
        s : array-like of shape (n_samples, n_classes-1)
            LDA projections.
        """
        
        self.fit(X,y)
        return self.transform(X)

    def get_params(self):
        """
        Return saved parameters.
        
        Returns
        -------
        out : dictionary
            Parameters
        """
        out = dict()
        if self.features_names_ is not None:
            out['features'] = self.features_names_
        out['eigenvalues']=self.evals_
        out['eigenvectors']=self.evecs_
        out['S_between']=self.S_b_
        out['S_within']=self.S_w_
        return out
    
    def set_regularization(self,sw_reg):
        """
        Set regularization for within-scatter matrix.

        Parameters
        ----------
        sw_reg : float
            Regularization value.

        Notes
        -----
        .. math:: S_w = S_w + \mathtt{sw_reg}\ \mathbf{1}. 
        
        """
        self.sw_reg = 0.05

    def set_features_names(self,names):
        """
        Set names of the features (useful for creating PLUMED input file)

        Parameters
        ----------
        reg : array-like of shape (n_descriptors)
            Features names
        
        """
        self.features_names_ = names

    def plumed_input(self):
        """
        Generate PLUMED input file
        
        Returns
        -------
        out : string
            PLUMED input file
        """
        out = "" 
        for i in range(len(self.evals_)):
            if len(self.evals_)==1:
                #print('lda: COMBINE ARG=', end='')
                out += 'lda: COMBINE ARG='
            else:
                #print(f'lda{i+1}: COMBINE ARG=', end='')
                out += f'lda{i+1}: COMBINE ARG='
            for j in range(self.d_):
                if self.features_names_ is None:
                    #print(f'x{j},',end='')
                    out += f'x{j},'
                else:
                    #print(f'{self.features_names_[j]},',end='')
                    out += f'{self.features_names_[j]},'
            #print("\b COEFFICIENTS=",end='') 
            out = out [:-1]
            out += " COEFFICIENTS="
            for j in range(self.d_):
                #print(np.round(self.evecs_[j,i].cpu().numpy(),6),end=',')
                out += str(np.round(self.evecs_[j,i].cpu().numpy(),6))+','
            #print('\b PERIODIC=NO')
            out = out [:-1]
            out += ' PERIODIC=NO'
        return out 


class DeepLDA(NeuralNetworkCV,LinearDiscriminant):
    """
    Neural network based discriminant CV. 
    Perform a non-linear featurization of the inputs with a neural-network and optimize it as to maximize Fisher's discriminant ratio.

    Attributes
    ----------
    d_ : int
        Number of classes
    evals_ : torch.Tensor
        LDA eigenvalues
    evecs_ : torch.Tensor
        LDA eignvectors
    S_b_ : torch.Tensor
        Between scatter matrix
    S_w_ : torch.Tensor
        Within scatter matrix
        
    Methods
    -------
    forward(x)
        Compute DeepLDA CV
    set_regularization(sw_reg,lorentzian_reg)
        Set magnitudes of regularizations.
    train()
        Train NN
    evaluate_dataset(x,label)
        Fit LDA and project data 
    get_params()
        Return saved parameters
    set_features_names(names) #TODO Add
        Set features names
    plumed_input() #TODO add
        Generate PLUMED input file
    """

    def __init__(self, layers, activation = 'relu'):
        super().__init__(layers=layers,activation=activation)

        #lorentzian reg
        self.lorentzian_reg = 0 
        self.sw_reg = 0.05
        
        #training logs
        self.epochs = 0
        self.loss_train = []
        self.loss_valid = []
        self.log_header = True

        #output
        self.output_hidden = False
           
    def get_params(self):
        """
        Return attached parameters.
        
        Returns
        -------
        out : dictionary
            Parameters
        """
        out = dict()
        out['device']=self.device_
        out['Sw regularization']=self.sw_reg
        out['Lorentzian regularization']=self.lorentzian_reg
        out['Early Stopping']=True if self.earlystopping_ is not None else False
        out['Input standardization']=self.normIn
        out['# epochs']=self.epochs
        return out

    def forward(self, x: torch.tensor) -> (torch.tensor):
        """
        Compute model output. First propagate input through the NN and then apply LDA.

        Parameters
        ----------
        x : torch.tensor
            input data

        Returns
        -------
        z : torch.tensor
            CVs

        See Also
        --------
        forward_nn : compute forward pass of NN module

        """
        z = self.forward_nn(x)
        if (not self.output_hidden ):
            z = self.transform(z)
        return z

    def regularization_lorentzian(self, x):
        """
        Compute lorentzian regularization on NN outputs. 

        Parameters
        ----------
        x : float
            input data
        """
        reg_loss = x.pow(2).sum().div( x.size(0) )
        reg_loss_lor = - self.lorentzian_reg / (1+(reg_loss-1).pow(2))
        return reg_loss_lor
    
    def loss_function(self,H,y,save_params=False):
        """
        Loss function for the DeepLDA CV. Correspond to maximizing the eigenvalue(s) of LDA plus a regularization on the NN outputs.

        Parameters
        ----------
        H : torch.tensor
            NN output
        y : torch.tensor
            labels
        save_params: bool
            save the eigenvalues/vectors of LDA into the model

        Returns
        -------
        loss : torch.tensor
            loss function
        """
        eigvals = self.LDA(H, y, save_params)
        loss = - eigvals[0] # TODO GENERALIZE TO MULTICLASS
        if self.lorentzian_reg > 0:
            loss += self.regularization_lorentzian(H)
        return loss
        
    def train_epoch(self, loader):
        """
        Function for training an epoch.

        Parameters
        ----------
        loader: DataLoader
            training set
        """
        for data in loader:
            # =================get data===================
            X,y = data[0].float().to(self.device_),data[1].long().to(self.device_)
            # =================forward====================
            H = self.forward_nn(X)
            # =================lda loss===================
            loss = self.loss_function(H,y,save_params=False)
            # =================backprop===================
            self.opt_.zero_grad()
            loss.backward()
            self.opt_.step()
        # ===================log======================
        self.epochs +=1
        
    def evaluate_dataset(self, data, save_params=False):
        """
        Loss function for the DeepLDA CV. Correspond to maximizing the eigenvalue(s) of LDA plus a regularization on the NN outputs.

        Parameters
        ----------
        data : array-like (data,labels)
            validation dataset
        save_params: bool
            save the eigenvalues/vectors of LDA into the model

        Returns
        -------
        loss : torch.tensor
            loss function
        """   
        with torch.no_grad():
            X,y = data[0].float().to(self.device_),data[1].long().to(self.device_)
            H  = self.forward_nn(X)
            loss = self.loss_function(H,y,save_params)
        return loss

    def set_regularization(self, sw_reg = 0.02, lorentzian_reg = None):
        """
        Set magnitude of regularizations for the training:
        - add identity matrix multiplied by `sw_reg` to within scatter S_w.
        - add lorentzian regularization to NN outputs with magnitude `lorentzian_reg`

        If `lorentzian_reg` is None, set it equal to `2./sw_reg`.

        Parameters
        ----------
        sw_reg : float
            Regularization value for S_w.
        lorentzian_reg: float
            Regularization for lorentzian on NN outputs.

        Notes
        -----
        These regularizations are described in [1]_.
        .. [1] Luigi Bonati, Valerio Rizzi, and Michele Parrinello, J. Phys. Chem. Lett. 11, 2998-3004 (2020).

        - S_w
        .. math:: S_w = S_w + \mathtt{sw_reg}\ \mathbf{1}. 
        
        - Lorentzian
        TODO Add equation
        
        """
        self.sw_reg = sw_reg
        if lorentzian_reg is None:
            self.lorentzian_reg = 2./sw_reg
        else:
            self.lorentzian_reg = lorentzian_reg
            
    def train(self, train_data, valid_data=None, standardize_inputs=True, batch_size=-1, nepochs=1000, log_every=1, info=False):
        """
        Train Deep-LDA CVs.

        Parameters
        ----------
        train_data: DataLoader
            training set
        valid_data: list of torch.tensors (X:input, y:labels)
            validation set
        standardize_inputs: bool
            whether to standardize input data
        batch_size: bool, optional
            number of points per batch (default = -1 --> single batch)
        nepochs: int, optional
            number of epochs (default = 1000)
        log_every: int, optional
            frequency of log (default = 1)
        print_info: bool, optional
            print debug info (default = False)
        """

        #check optimizer
        if self.opt_ is None:
            self.default_optimizer()
        
        #create dataloader
        train_dataset = ColvarDataset(train_data)
        if batch_size==-1:
            batch_size=len(train_data[0])
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)

        #standardize inputs
        if standardize_inputs:
            self.standardize_inputs(train_data[0])
            
        #print info
        if info:
            self.print_info()
        
        #train
        for ep in range(nepochs):
            self.train_epoch(train_loader)
            
            loss_train = self.evaluate_dataset(train_data,save_params=True)
            loss_valid = self.evaluate_dataset(valid_data)
            self.loss_train.append(loss_train)
            self.loss_valid.append(loss_valid)
            
            #earlystopping
            if self.earlystopping_ is not None:
                self.earlystopping_(loss_valid,self.parameters)
                
            #log
            if ((ep+1) % log_every == 0) or ( self.earlystopping_.early_stop ):
                self.print_log({'Epoch':ep+1,'Train Loss':loss_train,'Valid Loss':loss_valid},
                               spacing=[6,12,12],decimals=2)
                
            #check whether to stop
            if (self.earlystopping_ is not None) and (self.earlystopping_.early_stop):
                self.parameters = self.earlystopping_.best_model
                break

class ColvarDataset(Dataset):
    """
    COLVAR dataset
    
    """

    def __init__(self, colvar_list):
        self.nstates = len( colvar_list )
        self.colvar = colvar_list
        
    def __len__(self):
        return len(self.colvar[0])

    def __getitem__(self, idx):
        x = ()
        for i in range(self.nstates):
            x += (self.colvar[i][idx],)
        return x
    
#useful for cycling over the test dataset if it is smaller than the training set
def cycle(iterable):
    while True:
        for x in iterable:
            yield x
