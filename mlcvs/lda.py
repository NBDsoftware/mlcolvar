"""Linear discriminant analysis-based CVs."""

__all__ = ["LDA_CV", "DeepLDA_CV"]

import torch
from .models import LinearCV, NeuralNetworkCV
from torch.utils.data import Dataset, DataLoader


class LDA:
    """
    Fisher's discriminant class.

    Attributes
    ----------
    n_features : int
        Number of features
    n_classes : int
        Number of classes
    evals_ : torch.Tensor
        LDA eigenvalues
    evecs_ : torch.Tensor
        LDA eigenvectors
    S_b_ : torch.Tensor
        Between scatter matrix
    S_w_ : torch.Tensor
        Within scatter matrix
    sw_reg : float
        Regularization to S_w matrix

    Methods
    -------
    compute_LDA(H,y,save_params):
        Perform LDA
    """

    def __init__(self, harmonic_lda = False):
        """
        Create a LDA object

        Parameters
        ----------
        harmonic_lda : bool
            Harmonic variant of LDA
        """

        # initialize attributes
        self.harmonic_lda = harmonic_lda
        self.evals_ = None
        self.evecs_ = None
        self.S_b_ = None
        self.S_w_ = None
        self.n_features = None 
        self.n_classes = None

        # Regularization
        self.sw_reg = 1e-6

        # Initialize device and dtype
        self.dtype_ = torch.float32
        self.device_ = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    def compute_LDA(self, H, label, save_params=True):
        """
        Performs LDA and saves parameters.

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

        # sizes
        N, d = H.shape
        self.n_features = d

        # classes
        classes = torch.unique(label)
        n_classes = len(classes)
        self.n_classes = n_classes

        # Mean centered observations for entire population
        H_bar = H - torch.mean(H, 0, True)
        # Total scatter matrix (cov matrix over all observations)
        S_t = H_bar.t().matmul(H_bar) / (N - 1)
        # Define within scatter matrix and compute it
        S_w = torch.Tensor().new_zeros((d, d), device=self.device_, dtype=self.dtype_)
        if self.harmonic_lda:
            S_w_inv = torch.Tensor().new_zeros((d, d), device = self.device_, dtype = self.dtype_)
        # Loop over classes to compute means and covs
        for i in classes:
            # check which elements belong to class i
            H_i = H[torch.nonzero(label == i).view(-1)]
            # compute mean centered obs of class i
            H_i_bar = H_i - torch.mean(H_i, 0, True)
            # count number of elements
            N_i = H_i.shape[0]
            if N_i == 0:
                continue

            # LDA
            S_w += H_i_bar.t().matmul(H_i_bar) / ((N_i - 1) * n_classes)

            # HLDA
            if self.harmonic_lda:
                inv_i = H_i_bar.t().matmul(H_i_bar) / ((N_i - 1) * n_classes)
                S_w_inv += inv_i.inverse()

        if self.harmonic_lda:
            S_w = S_w_inv.inverse()

        # Compute S_b from total scatter matrix
        S_b = S_t - S_w

        # Regularize S_w
        S_w = S_w + self.sw_reg * torch.diag(
            torch.Tensor().new_ones((d), device=self.device_, dtype=self.dtype_)
        )

        # -- Generalized eigenvalue problem: S_b * v_i = lambda_i * Sw * v_i --

        # (1) use cholesky decomposition for S_w
        L = torch.cholesky(S_w, upper=False)

        # (2) define new matrix using cholesky decomposition
        L_t = torch.t(L)
        L_ti = torch.inverse(L_t)
        L_i = torch.inverse(L)
        S_new = torch.matmul(torch.matmul(L_i, S_b), L_ti)

        # (3) find eigenvalues and vectors of S_new
        eigvals, eigvecs = torch.symeig(S_new, eigenvectors=True)
        # sort
        eigvals, indices = torch.sort(eigvals, 0, descending=True)
        eigvecs = eigvecs[:, indices]

        # (4) return to original eigenvectors
        eigvecs = torch.matmul(L_ti, eigvecs)

        # normalize them
        for i in range(eigvecs.shape[1]):  # TODO maybe change in sum along axis?
            norm = eigvecs[:, i].pow(2).sum().sqrt()
            eigvecs[:, i].div_(norm)
        # set the first component positive
        eigvecs.mul_(torch.sign(eigvecs[0, :]).unsqueeze(0).expand_as(eigvecs))

        # keep only C-1 eigvals and eigvecs
        eigvals = eigvals[: n_classes - 1]
        eigvecs = eigvecs[:, : n_classes - 1]
        if save_params:
            self.evals_ = eigvals
            self.evecs_ = eigvecs
            self.S_b_ = S_b
            self.S_w_ = S_w

        return eigvals, eigvecs


class LDA_CV(LinearCV):
    """
    Linear Discriminant-based collective variable.

    Attributes
    ----------
    lda: LDA object
        Linear discriminant analysis instance.

    Methods
    -------
    train(X,y)
        Fit LDA given data and classes
    train_forward(X,y)
        Fit LDA and project along components

    + LinearCV TODO CHECK
    """

    def __init__(self, n_features, harmonic_lda = False, device="auto", dtype=torch.float32, **kwargs):
        """
        Create a LDA_CV object

        Parameters
        ----------
        n_features : int
            Number of input features
        harmonic_lda : bool
            Build a HLDA CV.
        **kwargs : dict
            Additional parameters for LinearCV object 
        """
        super().__init__(n_features=n_features, device=device, dtype=dtype, **kwargs)

        self.name_ = "hlda_cv" if harmonic_lda else "lda_cv"
        self.lda = LDA(harmonic_lda)

    def train(self, X, y):
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
            X = torch.tensor(X, dtype=self.dtype_, device=self.device_)
        if type(y) != torch.Tensor:
            y = torch.tensor(y, device=self.device_)  # class labels are integers
        _, eigvecs = self.lda.compute_LDA(X, y)
        # save parameters for estimator
        self.w = eigvecs

    def train_forward(self, X, y):
        """
        Fit LDA and project along components.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Training data.
        y : array-like of shape (n_samples,)
            Classes labels.

        Returns
        -------
        s : array-like of shape (n_samples, n_classes-1)
            Linear projection of inputs.

        """
        self.train(X, y)
        return self.forward(X)

    def set_regularization(self, sw_reg):
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
        self.lda.sw_reg = 0.05


class DeepLDA_CV(NeuralNetworkCV):
    """
    Neural network based discriminant CV.
    Perform a non-linear featurization of the inputs with a neural-network and optimize it as to maximize Fisher's discriminant ratio.

    Attributes
    ----------
    lda : LDA object
        Number of classes
    lorentzian_reg: float
        Magnitude of lorentzian regularization
    epochs : int
        Number of performed epochs of training
    loss_train : list
        Loss function for train data over training. 
    loss-valid : list
        Loss function for validation data over training.

    Methods
    -------
    __init__(layers,activation,**kwargs)
        Create a DeepLDA_CV object
    set_regularization(sw_reg,lorentzian_reg)
        Set magnitudes of regularizations.
    regularization_lorentzian(H)
        Apply regularization to NN output.
    loss_function(H,y)
        Loss function for the DeepLDA CV.
    train()
        Train Deep-LDA CVs.
    evaluate_dataset(x,label)
        Evaluate loss function on dataset.
    """

    def __init__(self, layers, activation="relu", device="auto", dtype=torch.float32, **kwargs):
        """
        Create a DeepLDA_CV object

        Parameters
        ----------
        n_features : int
            Number of input features
        **kwargs : dict
            Additional parameters for LinearCV object
        """
        
        super().__init__(
            layers=layers, activation=activation, device=device, dtype=dtype, **kwargs
        )
        self.name_ = "deeplda_cv"
        self.lda = LDA()

        # lorentzian regularization
        self.lorentzian_reg = 0

        # training logs
        self.epochs = 0
        self.loss_train = []
        self.loss_valid = []
        self.log_header = True

    def set_regularization(self, sw_reg=0.02, lorentzian_reg=None):
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
        self.lda.sw_reg = sw_reg
        if lorentzian_reg is None:
            self.lorentzian_reg = 2.0 / sw_reg
        else:
            self.lorentzian_reg = lorentzian_reg

    def regularization_lorentzian(self, H):
        """
        Compute lorentzian regularization on NN outputs.

        Parameters
        ----------
        x : float
            input data
        """
        reg_loss = H.pow(2).sum().div(H.size(0))
        reg_loss_lor = -self.lorentzian_reg / (1 + (reg_loss - 1).pow(2))
        return reg_loss_lor

    def loss_function(self, H, y, save_params=False):
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
        eigvals, eigvecs = self.lda.compute_LDA(H, y, save_params)
        if save_params:
            self.w = eigvecs

        # TODO add sum option for multiclass

        # if two classes loss is equal to the single eigenvalue
        if self.lda.n_classes == 2:
            loss = -eigvals
        # if more than two classes loss equal to the smallest of the C-1 eigenvalues
        elif self.lda.n_classes > 2:
            loss = -eigvals[self.lda.n_classes - 2]
        else:
            raise ValueError("The number of classes for LDA must be greater than 1")

        if self.lorentzian_reg > 0:
            loss += self.regularization_lorentzian(H)
        return loss

    def train_epoch(self, loader):
        """
        Auxiliary function for training an epoch.

        Parameters
        ----------
        loader: DataLoader
            training set
        """
        for data in loader:
            # =================get data===================
            X, y = data[0].float().to(self.device_), data[1].long().to(self.device_)
            # =================forward====================
            H = self.forward_nn(X)
            # =================lda loss===================
            loss = self.loss_function(H, y, save_params=False)
            # =================backprop===================
            self.opt_.zero_grad()
            loss.backward()
            self.opt_.step()
        # ===================log======================
        self.epochs += 1

    def train(
        self,
        train_data,
        valid_data=None,
        standardize_inputs=True,
        batch_size=-1,
        nepochs=1000,
        log_every=1,
        info=False,
    ):
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
            number of points per batch (default = -1, single batch)
        nepochs: int, optional
            number of epochs (default = 1000)
        log_every: int, optional
            frequency of log (default = 1)
        print_info: bool, optional
            print debug info (default = False)
        """

        # check optimizer
        if self.opt_ is None:
            self.default_optimizer()

        # create dataloader
        train_dataset = ColvarDataset(train_data)
        if batch_size == -1:
            batch_size = len(train_data[0])
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)

        # standardize inputs
        if standardize_inputs:
            self.standardize_inputs(train_data[0])

        # print info
        if info:
            self.print_info()

        # train
        for ep in range(nepochs):
            self.train_epoch(train_loader)

            loss_train = self.evaluate_dataset(train_data, save_params=True)
            loss_valid = self.evaluate_dataset(valid_data)
            self.loss_train.append(loss_train)
            self.loss_valid.append(loss_valid)

            # earlystopping
            if self.earlystopping_ is not None:
                self.earlystopping_(loss_valid, self.parameters)

            # log
            if ((ep + 1) % log_every == 0) or (self.earlystopping_.early_stop):
                self.print_log(
                    {
                        "Epoch": ep + 1,
                        "Train Loss": loss_train,
                        "Valid Loss": loss_valid,
                    },
                    spacing=[6, 12, 12],
                    decimals=2,
                )

            # check whether to stop
            if (self.earlystopping_ is not None) and (self.earlystopping_.early_stop):
                self.parameters = self.earlystopping_.best_model
                break

    def evaluate_dataset(self, data, save_params=False):
        """
        Evaluate loss function on dataset.

        Parameters
        ----------
        data : array-like (data,labels)
            validation dataset
        save_params: bool
            save the eigenvalues/vectors of LDA into the model

        Returns
        -------
        loss : torch.tensor
            loss value
        """
        with torch.no_grad():
            X, y = data[0].float().to(self.device_), data[1].long().to(self.device_)
            H = self.forward_nn(X)
            loss = self.loss_function(H, y, save_params)
        return loss


class ColvarDataset(Dataset):
    """
    Auxiliary dataset to generate a dataloader.

    """

    def __init__(self, colvar_list):
        """
        Initialize dataset

        Parameters
        ----------
        colvar_list : list of arrays
            input data (also with classes)
        """
        self.nstates = len(colvar_list)
        self.colvar = colvar_list

    def __len__(self):
        return len(self.colvar[0])

    def __getitem__(self, idx):
        x = ()
        for i in range(self.nstates):
            x += (self.colvar[i][idx],)
        return x
