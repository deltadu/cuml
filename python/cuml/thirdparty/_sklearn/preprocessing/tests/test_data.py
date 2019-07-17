#
# Copyright (c) 2019, NVIDIA CORPORATION.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import pytest
import cupy as cp
import cudf
import numpy as np
import numpy.linalg as la
from numbers import Number
import warnings

from sklearn.utils import gen_batches
from sklearn.utils.testing import assert_raises
from sklearn.utils.testing import assert_almost_equal
from sklearn.utils.testing import assert_array_almost_equal
from sklearn.utils.testing import assert_greater_equal
from sklearn.utils.testing import assert_less_equal
from sklearn.utils.testing import assert_allclose
from sklearn.utils.testing import assert_array_less
from sklearn.utils.testing import assert_raises_regex
# from sklearn.utils.testing import clean_warning_registry
# from sklearn.utils.testing import assert_raise_message
# from sklearn.utils.testing import assert_warns_message
# from sklearn.utils.testing import assert_array_equal
# from sklearn.utils.testing import assert_no_warnings
# from sklearn.utils.testing import assert_allclose_dense_sparse

from cuml.thirdparty._sklearn.preprocessing.data import (
    _handle_zeros_in_scale, to_cupy)

from cuml.thirdparty import (
    StandardScaler, scale, MinMaxScaler, minmax_scale, Binarizer,
    KernelCenterer, Normalizer, normalize, MaxAbsScaler, maxabs_scale,
    RobustScaler, robust_scale, add_dummy_feature)

from sklearn import datasets
iris = datasets.load_iris()

# Make some data to be used many times
rng = cp.random.RandomState(0)
n_features = 30
n_samples = 1000
offsets = rng.uniform(-1, 1, size=n_features)
scales = rng.uniform(1, 10, size=n_features)
X_2d = rng.randn(n_samples, n_features) * scales + offsets
X_1row = X_2d[0, :].reshape(1, n_features)
X_1col = X_2d[:, 0].reshape(n_samples, 1)
X_1row_cudf = cudf.DataFrame.from_gpu_matrix(X_1row.copy())
X_1col_cudf = cudf.DataFrame.from_gpu_matrix(X_1col.copy())


def assert_array_equal(a, b, tol=1e-4, with_sign=True):
    a, b = to_cparray(a), to_cparray(b)
    if not with_sign:
        a, b = cp.abs(a), cp.abs(b)
    res = cp.max(cp.abs(a - b)) < tol
    assert res.all()


def to_cparray(x):
    if isinstance(x, Number):
        return cp.array([x])
    elif isinstance(x, cp.ndarray):
        return x
    elif isinstance(x, cudf.DataFrame):
        return cp.array(x.as_gpu_matrix())
    elif isinstance(x, (cudf.Series, list)):
        return cp.array(x)
    else:
        raise TypeError('input of type {} is not cudf or cupy'.format(type))


def assert_correct_incr(i, batch_start, batch_stop, n, chunk_size,
                        n_samples_seen):
    if batch_stop != n:
        assert_array_equal((i + 1) * chunk_size, n_samples_seen)
    else:
        assert_array_equal(i * chunk_size + (batch_stop - batch_start),
                           n_samples_seen)


def test_standard_scaler_1d():
    # Test scaling of dataset along single axis
    for X in [X_1row, X_1col, X_1row_cudf, X_1col_cudf]:
        scaler = StandardScaler()
        X_scaled = scaler.fit(X).transform(X, copy=True)

        X, _ = to_cupy(X)
        X_scaled, _ = to_cupy(X_scaled)
        if X.shape[0] == 1:
            assert_array_equal(scaler.mean_, X.ravel())
            assert_array_equal(scaler.scale_, cp.ones(n_features))
            assert_array_equal(X_scaled.mean(axis=0),
                               cp.zeros_like(X_scaled.mean(axis=0)))
            assert_array_equal(X_scaled.std(axis=0),
                               cp.zeros_like(X_scaled.std(axis=0)))
        else:
            assert_array_equal(scaler.mean_, X.mean())
            assert_array_equal(scaler.scale_, X.std())
            assert_array_equal(X_scaled.mean(axis=0),
                               cp.zeros_like(X_scaled.mean(axis=0)))
            assert_array_equal(X_scaled.mean(axis=0), .0)
            assert_array_equal(X_scaled.std(axis=0), 1.)
        assert_array_equal(scaler.n_samples_seen_, X.shape[0])

        # check inverse transform
        X_scaled_back = scaler.inverse_transform(X_scaled)
        assert_array_equal(X_scaled_back, X)

    # Constant feature
    X = cp.ones((5, 1))
    scaler = StandardScaler()
    X_scaled = scaler.fit(X).transform(X, copy=True)
    assert_array_equal(scaler.mean_, 1.)
    assert_array_equal(scaler.scale_, 1.)
    assert_array_equal(X_scaled.mean(axis=0), .0)
    assert_array_equal(X_scaled.std(axis=0), .0)
    assert_array_equal(scaler.n_samples_seen_, X.shape[0])


def test_standard_scaler_dtype():
    # Ensure scaling does not affect dtype
    rng = cp.random.RandomState(0)
    n_samples = 10
    n_features = 3
    for dtype in [cp.float16, cp.float32, cp.float64]:
        X = rng.randn(n_samples, n_features).astype(dtype)
        scaler = StandardScaler()
        X_scaled = scaler.fit(X).transform(X)
        assert X.dtype == X_scaled.dtype
        assert scaler.mean_.dtype == np.float64
        assert scaler.scale_.dtype == np.float64


def test_scale_1d():
    # 1-d inputs
    X_arr = cp.array([1., 3., 5., 0.])
    X_cudf = cudf.from_dlpack(X_arr.copy().toDlpack())

    for X in [X_arr, X_cudf]:
        X_scaled, _ = to_cupy(scale(X))
        # after X_scaled rendered, convert X to cupy to compare with X_scaled
        X, _ = to_cupy(X)
        assert_array_equal(X_scaled.mean(), 0.0)
        assert_array_equal(X_scaled.std(), 1.0)
        assert_array_equal(scale(X, with_mean=False, with_std=False), X)


def test_scaler_2d_arrays():
    # Test scaling of 2d array along first axis
    rng = cp.random.RandomState(0)
    n_features = 5
    n_samples = 4
    X = rng.randn(n_samples, n_features)
    X[:, 0] = 0.0  # first feature is always of zero
    X_cudf = cudf.DataFrame.from_gpu_matrix(cp.asfortranarray(X))

    for X in [X, X_cudf]:
        scaler = StandardScaler()
        X_scaled = scaler.fit(X).transform(X, copy=True)

        X_scaled, _ = to_cupy(X_scaled)
        X, _ = to_cupy(X)
        assert not cp.any(cp.isnan(X_scaled))
        assert_array_equal(scaler.n_samples_seen_, n_samples)

        assert_array_equal(X_scaled.mean(axis=0), n_features * [0.0])
        assert_array_equal(X_scaled.std(axis=0), [0., 1., 1., 1., 1.])
        # Check that X has been copied
        assert X_scaled is not X

        # check inverse transform
        X_scaled_back = scaler.inverse_transform(X_scaled)
        assert X_scaled_back is not X
        assert X_scaled_back is not X_scaled
        assert_array_equal(X_scaled_back, X)

        X_scaled = scale(X, axis=1, with_std=False)
        assert not cp.any(cp.isnan(X_scaled))
        assert_array_equal(X_scaled.mean(axis=1), n_samples * [0.0])
        X_scaled = scale(X, axis=1, with_std=True)
        assert not cp.any(cp.isnan(X_scaled))
        assert_array_equal(X_scaled.mean(axis=1), n_samples * [0.0])
        assert_array_equal(X_scaled.std(axis=1), n_samples * [1.0])
        # Check that the data hasn't been modified
        assert X_scaled is not X

        X_scaled = scaler.fit(X).transform(X, copy=False)
        assert not cp.any(cp.isnan(X_scaled))
        assert_array_equal(X_scaled.mean(axis=0), n_features * [0.0])
        assert_array_equal(X_scaled.std(axis=0), [0., 1., 1., 1., 1.])
        # Check that X has not been copied
        assert X_scaled is X

        X = rng.randn(4, 5)
        X[:, 0] = 1.0  # first feature is a constant, non zero feature
        scaler = StandardScaler()
        X_scaled = scaler.fit(X).transform(X, copy=True)
        assert not cp.any(cp.isnan(X_scaled))
        assert_array_equal(X_scaled.mean(axis=0), n_features * [0.0])
        assert_array_equal(X_scaled.std(axis=0), [0., 1., 1., 1., 1.])
        # Check that X has not been copied
        assert X_scaled is not X


def test_scaler_float16_overflow():
    # Test if the scaler will not overflow on float16 numpy arrays
    rng = cp.random.RandomState(0)
    # float16 has a maximum of 65500.0. On the worst case 5 * 200000 is 100000
    # which is enough to overflow the data type
    X = rng.uniform(5, 10, [200000, 1]).astype(cp.float16)

    scaler = StandardScaler().fit(X)
    X_scaled = scaler.transform(X)

    # Calculate the float64 equivalent to verify result
    X_scaled_f64 = StandardScaler().fit_transform(X.astype(cp.float64))

    # Overflow calculations may cause -inf, inf, or nan. Since there is no nan
    # icput, all of the outputs should be finite. This may be redundant since a
    # FloatingPointError exception will be thrown on overflow above.
    assert cp.all(cp.isfinite(X_scaled))

    # The normal distribution is very unlikely to go above 4. At 4.0-8.0 the
    # float16 precision is 2^-8 which is around 0.004. Thus only 2 decimals are
    # checked to account for precision differences.
    np.testing.assert_array_almost_equal(
        cp.asnumpy(X_scaled),
        cp.asnumpy(X_scaled_f64),
        decimal=2)


def test_handle_zeros_in_scale():
    s1 = cp.array([0, 1, 2, 3])
    s2 = _handle_zeros_in_scale(s1, copy=True)

    assert not s1[0] == s2[0]
    assert_array_equal(s1, cp.array([0, 1, 2, 3]))
    assert_array_equal(s2, cp.array([1, 1, 2, 3]))


def test_minmax_scaler_partial_fit():
    # Test if partial_fit run over many batches of size 1 and 50
    # gives the same results as fit
    X = X_2d
    n = X.shape[0]

    for chunk_size in [1, 2, 50, n, n + 42]:
        # Test mean at the end of the process
        scaler_batch = MinMaxScaler().fit(X)

        scaler_incr = MinMaxScaler()
        for batch in gen_batches(n_samples, chunk_size):
            scaler_incr = scaler_incr.partial_fit(X[batch])

        assert_array_equal(scaler_batch.data_min_,
                           scaler_incr.data_min_)
        assert_array_equal(scaler_batch.data_max_,
                           scaler_incr.data_max_)
        assert_array_equal(scaler_batch.n_samples_seen_,
                           scaler_incr.n_samples_seen_)
        assert_array_equal(scaler_batch.data_range_,
                           scaler_incr.data_range_)
        assert_array_equal(scaler_batch.scale_, scaler_incr.scale_)
        assert_array_equal(scaler_batch.min_, scaler_incr.min_)

        # Test std after 1 step
        batch0 = slice(0, chunk_size)
        scaler_batch = MinMaxScaler().fit(X[batch0])
        scaler_incr = MinMaxScaler().partial_fit(X[batch0])

        assert_array_equal(scaler_batch.data_min_,
                           scaler_incr.data_min_)
        assert_array_equal(scaler_batch.data_max_,
                           scaler_incr.data_max_)
        assert_array_equal(scaler_batch.n_samples_seen_,
                           scaler_incr.n_samples_seen_)
        assert_array_equal(scaler_batch.data_range_,
                           scaler_incr.data_range_)
        assert_array_equal(scaler_batch.scale_, scaler_incr.scale_)
        assert_array_equal(scaler_batch.min_, scaler_incr.min_)

        # Test std until the end of partial fits, and
        scaler_batch = MinMaxScaler().fit(X)
        scaler_incr = MinMaxScaler()  # Clean estimator
        for i, batch in enumerate(gen_batches(n_samples, chunk_size)):
            scaler_incr = scaler_incr.partial_fit(X[batch])
            assert_correct_incr(i, batch_start=batch.start,
                                batch_stop=batch.stop, n=n,
                                chunk_size=chunk_size,
                                n_samples_seen=scaler_incr.n_samples_seen_)


def test_standard_scaler_partial_fit():
    # Test if partial_fit run over many batches of size 1 and 50
    # gives the same results as fit
    X = X_2d
    n = X.shape[0]

    for chunk_size in [1, 2, 50, n, n + 42]:
        # Test mean at the end of the process
        scaler_batch = StandardScaler(with_std=False).fit(X)

        scaler_incr = StandardScaler(with_std=False)
        for batch in gen_batches(n_samples, chunk_size):
            scaler_incr = scaler_incr.partial_fit(X[batch])

        assert_array_equal(scaler_batch.mean_, scaler_incr.mean_)
        assert scaler_batch.var_ == scaler_incr.var_    # Nones
        assert_array_equal(scaler_batch.n_samples_seen_,
                           scaler_incr.n_samples_seen_)

        # Test std after 1 step
        batch0 = slice(0, chunk_size)
        scaler_incr = StandardScaler().partial_fit(X[batch0])
        if chunk_size == 1:
            assert_array_equal(cp.zeros(n_features, dtype=cp.float64),
                               scaler_incr.var_)
            assert_array_equal(cp.ones(n_features, dtype=cp.float64),
                               scaler_incr.scale_)
        else:
            assert_array_equal(cp.var(X[batch0], axis=0),
                               scaler_incr.var_)
            assert_array_equal(cp.std(X[batch0], axis=0),
                               scaler_incr.scale_)  # no constants

        # Test std until the end of partial fits, and
        scaler_batch = StandardScaler().fit(X)
        scaler_incr = StandardScaler()  # Clean estimator
        for i, batch in enumerate(gen_batches(n_samples, chunk_size)):
            scaler_incr = scaler_incr.partial_fit(X[batch])
            assert_correct_incr(i, batch_start=batch.start,
                                batch_stop=batch.stop, n=n,
                                chunk_size=chunk_size,
                                n_samples_seen=scaler_incr.n_samples_seen_)

        assert_array_equal(scaler_batch.var_, scaler_incr.var_)
        assert_array_equal(scaler_batch.n_samples_seen_,
                           scaler_incr.n_samples_seen_)


def test_standard_scaler_partial_fit_numerical_stability():
    # Test if the incremental computation introduces significative errors
    # for large datasets with values of large magniture
    rng = cp.random.RandomState(0)
    n_features = 2
    n_samples = 100
    offsets = rng.uniform(-1e15, 1e15, size=n_features)
    scales = rng.uniform(1e3, 1e6, size=n_features)
    X = rng.randn(n_samples, n_features) * scales + offsets

    scaler_batch = StandardScaler().fit(X)
    scaler_incr = StandardScaler()
    for chunk in X:
        scaler_incr = scaler_incr.partial_fit(chunk.reshape(1, n_features))

    # Regardless of abs values, they must not be more diff 6 significant digits
    tol = 10 ** (-6)
    assert_allclose(
        cp.asnumpy(scaler_incr.mean_),
        cp.asnumpy(scaler_batch.mean_), rtol=tol)
    assert_allclose(
        cp.asnumpy(scaler_incr.var_),
        cp.asnumpy(scaler_batch.var_), rtol=tol)
    assert_allclose(
        cp.asnumpy(scaler_incr.scale_),
        cp.asnumpy(scaler_batch.scale_), rtol=tol)
    # NOTE Be aware that for much larger offsets std is very unstable (last
    # assert) while mean is OK.


def test_standard_scaler_trasform_with_partial_fit():
    # Check some postconditions after applying partial_fit and transform
    X = X_2d[:100, :]

    scaler_incr = StandardScaler()
    for i, batch in enumerate(gen_batches(X.shape[0], 1)):

        X_sofar = X[:(i + 1), :]
        chunks_copy = X_sofar.copy()
        scaled_batch = StandardScaler().fit_transform(X_sofar)

        scaler_incr = scaler_incr.partial_fit(X[batch])
        scaled_incr = scaler_incr.transform(X_sofar)

        assert_array_equal(scaled_batch, scaled_incr)
        assert_array_equal(X_sofar, chunks_copy)     # No change
        right_input = scaler_incr.inverse_transform(scaled_incr)
        assert_array_equal(X_sofar, right_input)

        zero = np.zeros(X.shape[1])
        epsilon = np.finfo(float).eps
        assert_array_less(
            zero,
            cp.asnumpy(scaler_incr.var_) + epsilon)  # as less or equal
        assert_array_less(
            zero,
            cp.asnumpy(scaler_incr.scale_) + epsilon)
        # (i+1) because the Scaler has been already fitted
        assert_array_equal((i + 1), scaler_incr.n_samples_seen_)


def test_min_max_scaler_iris():
    X = cp.array(iris.data)
    scaler = MinMaxScaler()
    # default params
    X_trans = scaler.fit_transform(X)
    assert_array_equal(X_trans.min(axis=0), 0)
    assert_array_equal(X_trans.max(axis=0), 1)
    X_trans_inv = scaler.inverse_transform(X_trans)
    assert_array_equal(X, X_trans_inv)

    # not default params: min=1, max=2
    scaler = MinMaxScaler(feature_range=(1, 2))
    X_trans = scaler.fit_transform(X)
    assert_array_equal(X_trans.min(axis=0), 1)
    assert_array_equal(X_trans.max(axis=0), 2)
    X_trans_inv = scaler.inverse_transform(X_trans)
    assert_array_equal(X, X_trans_inv)

    # min=-.5, max=.6
    scaler = MinMaxScaler(feature_range=(-.5, .6))
    X_trans = scaler.fit_transform(X)
    assert_array_equal(X_trans.min(axis=0), -.5)
    assert_array_equal(X_trans.max(axis=0), .6)
    X_trans_inv = scaler.inverse_transform(X_trans)
    assert_array_equal(X, X_trans_inv)

    # raises on invalid range
    scaler = MinMaxScaler(feature_range=(2, 1))
    with pytest.raises(ValueError):
        scaler.fit(X)


def test_min_max_scaler_zero_variance_features():
    # Check min max scaler on toy data with zero variance features
    X = cp.array([[0., 1., +0.5],
                  [0., 1., -0.1],
                  [0., 1., +1.1]])

    X_new = cp.array([[+0., 2., 0.5],
                      [-1., 1., 0.0],
                      [+0., 1., 1.5]])

    # default params
    scaler = MinMaxScaler()
    X_trans = scaler.fit_transform(X)
    X_expected_0_1 = [[0., 0., 0.5],
                      [0., 0., 0.0],
                      [0., 0., 1.0]]
    assert_array_equal(X_trans, X_expected_0_1)
    X_trans_inv = scaler.inverse_transform(X_trans)
    assert_array_equal(X, X_trans_inv)

    X_trans_new = scaler.transform(X_new)
    X_expected_0_1_new = [[+0., 1., 0.500],
                          [-1., 0., 0.083],
                          [+0., 0., 1.333]]
    np.testing.assert_array_almost_equal(
        cp.asnumpy(X_trans_new),
        cp.asnumpy(X_expected_0_1_new), decimal=2)

    # not default params
    scaler = MinMaxScaler(feature_range=(1, 2))
    X_trans = scaler.fit_transform(X)
    X_expected_1_2 = [[1., 1., 1.5],
                      [1., 1., 1.0],
                      [1., 1., 2.0]]
    assert_array_equal(X_trans, X_expected_1_2)

    # function interface
    X_trans = minmax_scale(X)
    assert_array_equal(X_trans, X_expected_0_1)
    X_trans = minmax_scale(X, feature_range=(1, 2))
    assert_array_equal(X_trans, X_expected_1_2)


def test_minmax_scale_axis1():
    X = cp.array(iris.data)
    X_trans = minmax_scale(X, axis=1)
    assert_array_equal(cp.min(X_trans, axis=1), 0)
    assert_array_equal(cp.max(X_trans, axis=1), 1)


def test_min_max_scaler_1d():
    # Test scaling of dataset along single axis
    for X in [X_1row, X_1col, X_1row_cudf, X_1col_cudf]:

        scaler = MinMaxScaler(copy=True)
        X_scaled = scaler.fit(X).transform(X)
        X_scaled, _ = to_cupy(X_scaled)

        if X.shape[0] == 1:
            assert_array_equal(X_scaled.min(axis=0), cp.zeros(n_features))
            assert_array_equal(X_scaled.max(axis=0), cp.zeros(n_features))
        else:
            assert_array_equal(X_scaled.min(axis=0), .0)
            assert_array_equal(X_scaled.max(axis=0), 1.)
        assert scaler.n_samples_seen_ == X.shape[0]

        # check inverse transform
        X_scaled_back = scaler.inverse_transform(X_scaled)
        assert_array_equal(X_scaled_back, X)

    # Constant feature
    X = cp.ones((5, 1))
    scaler = MinMaxScaler()
    X_scaled = scaler.fit(X).transform(X)
    assert_greater_equal(cp.asnumpy(X_scaled.min()), 0.)
    assert_less_equal(cp.asnumpy(X_scaled.max()), 1.)
    assert scaler.n_samples_seen_ == X.shape[0]

    # Function interface
    X_1d = X_1row.ravel()
    min_ = X_1d.min()
    max_ = X_1d.max()
    assert_array_equal((X_1d - min_) / (max_ - min_),
                       minmax_scale(X_1d, copy=True))


def test_scaler_without_centering():
    rng = cp.random.RandomState(42)
    X = rng.randn(4, 5)
    X[:, 0] = 0.0  # first feature is always of zero

    scaler = StandardScaler(with_mean=False).fit(X)
    X_scaled = scaler.transform(X, copy=True)
    assert not cp.any(cp.isnan(X_scaled))

    assert_array_almost_equal(cp.asnumpy(X_scaled.mean(axis=0)),
                              np.array([0., -0.85, -0.77, -1.44, 0.2]),
                              decimal=2)

    assert_array_equal(X_scaled.std(axis=0),
                       cp.array([0., 1., 1., 1., 1.]))

    # Check that X has not been modified (copy)
    assert X_scaled is not X

    X_scaled_back = scaler.inverse_transform(X_scaled)
    assert X_scaled_back is not X
    assert X_scaled_back is not X_scaled
    assert_array_equal(X_scaled_back, X)


def _check_identity_scalers_attributes(scaler_1, scaler_2):
    assert scaler_1.mean_ is scaler_2.mean_ is None
    assert scaler_1.var_ is scaler_2.var_ is None
    assert scaler_1.scale_ is scaler_2.scale_ is None
    assert scaler_1.n_samples_seen_ == scaler_2.n_samples_seen_


def test_scaler_int():
    # test that scaler converts integer input to floating
    # for both sparse and dense matrices
    rng = cp.random.RandomState(42)
    X = rng.randint(20, size=(4, 5))
    X[:, 0] = 0  # first feature is always of zero

    with warnings.catch_warnings(record=True):
        scaler = StandardScaler(with_mean=False).fit(X)
        X_scaled = scaler.transform(X, copy=True)
    assert not cp.any(cp.isnan(X_scaled))

    assert_array_almost_equal(
        cp.asnumpy(X_scaled.mean(axis=0)),
        np.array([0., 3.18, 1.94, 1.53, 1.06]), decimal=2)
    assert_array_equal(X_scaled.std(axis=0), cp.array([0., 1., 1., 1., 1.]))

    # Check that X has not been modified (copy)
    assert X_scaled is not X

    X_scaled_back = scaler.inverse_transform(X_scaled)
    assert X_scaled_back is not X
    assert X_scaled_back is not X_scaled
    assert_array_equal(X_scaled_back, X)


def test_scaler_without_copy():
    # Check that StandardScaler.fit does not change input
    rng = cp.random.RandomState(42)
    X = rng.randn(4, 5)
    X[:, 0] = 0.0  # first feature is always of zero

    X_copy = X.copy()
    StandardScaler(copy=False).fit(X)
    assert_array_equal(X, X_copy)


def test_scale_input_finiteness_validation():
    # Check if non finite inputs raise ValueError
    X = cp.array([[cp.inf, 5, 6, 7, 8]])
    assert_raises_regex(ValueError,
                        "Input contains NaN, infinity or a value too large",
                        scale, X)


def test_scale_function_without_centering():
    rng = cp.random.RandomState(42)
    X = rng.randn(4, 5)
    X[:, 0] = 0.0  # first feature is always of zero

    X_scaled = scale(X, with_mean=False)
    assert not cp.any(cp.isnan(X_scaled))

    assert_array_almost_equal(cp.asnumpy(X_scaled.mean(axis=0)),
                              np.array([0., -0.85, -0.77, -1.44, 0.2]), 2)
    assert_array_equal(X_scaled.std(axis=0), cp.array([0., 1., 1., 1., 1.]))
    # Check that X has not been copied
    assert X_scaled is not X


def test_maxabs_scaler_zero_variance_features():
    # Check MaxAbsScaler on toy data with zero variance features
    X = cp.array([[0., 1., +0.5],
                  [0., 1., -0.3],
                  [0., 1., +1.5],
                  [0., 0., +0.0]])

    scaler = MaxAbsScaler()
    X_trans = scaler.fit_transform(X)
    X_expected = cp.array([[0., 1., 1.0 / 3.0],
                           [0., 1., -0.2],
                           [0., 1., 1.0],
                           [0., 0., 0.0]])
    assert_array_equal(X_trans, X_expected)
    X_trans_inv = scaler.inverse_transform(X_trans)
    assert_array_equal(X, X_trans_inv)

    # make sure new data gets transformed correctly
    X_new = cp.array([[+0., 2., 0.5],
                      [-1., 1., 0.0],
                      [+0., 1., 1.5]])
    X_trans_new = scaler.transform(X_new)
    X_expected_new = np.array([[+0., 2.0, 1.0 / 3.0],
                               [-1., 1.0, 0.0],
                               [+0., 1.0, 1.0]])

    assert_array_almost_equal(cp.asnumpy(X_trans_new),
                              X_expected_new, decimal=2)

    # function interface
    X_trans = maxabs_scale(X)
    assert_array_equal(X_trans, X_expected)


def test_maxabs_scaler_large_negative_value():
    # Check MaxAbsScaler on toy data with a large negative value
    X = cp.array([[0., 1., +0.5, -1.0],
                  [0., 1., -0.3, -0.5],
                  [0., 1., -100.0, 0.0],
                  [0., 0., +0.0, -2.0]])

    scaler = MaxAbsScaler()
    X_trans = scaler.fit_transform(X)
    X_expected = cp.array([[0., 1., 0.005, -0.5],
                           [0., 1., -0.003, -0.25],
                           [0., 1., -1.0, 0.0],
                           [0., 0., 0.0, -1.0]])
    assert_array_equal(X_trans, X_expected)


def test_maxabs_scaler_1d():
    # Test scaling of dataset along single axis
    for X in [X_1row, X_1col, X_1row_cudf, X_1col_cudf]:

        scaler = MaxAbsScaler(copy=True)
        X_scaled = scaler.fit(X).transform(X)

        X, _ = to_cupy(X)
        X_scaled, _ = to_cupy(X_scaled)
        if X.shape[0] == 1:
            assert_array_equal(cp.abs(X_scaled.max(axis=0)),
                               cp.ones(n_features))
        else:
            assert_array_equal((cp.abs(X_scaled)).max(axis=0), 1.)
        assert scaler.n_samples_seen_ == X.shape[0]

        # check inverse transform
        X_scaled_back = scaler.inverse_transform(X_scaled)
        assert_array_equal(X_scaled_back, X)

    # Constant feature
    X = cp.ones((5, 1))
    scaler = MaxAbsScaler()
    X_scaled = scaler.fit(X).transform(X)
    assert_array_equal(cp.abs(X_scaled.max(axis=0)), 1.)
    assert scaler.n_samples_seen_ == X.shape[0]

    # function interface
    X_1d = X_1row.ravel()
    max_abs = cp.abs(X_1d).max()
    assert_array_equal(X_1d / max_abs, maxabs_scale(X_1d, copy=True))


def test_maxabs_scaler_partial_fit():
    # Test if partial_fit run over many batches of size 1 and 50
    # gives the same results as fit
    X = X_2d[:100, :]
    n = X.shape[0]

    for chunk_size in [1, 2, 50, n, n + 42]:
        # Test mean at the end of the process
        scaler_batch = MaxAbsScaler().fit(X)

        scaler_incr = MaxAbsScaler()
        for batch in gen_batches(n, chunk_size):
            scaler_incr = scaler_incr.partial_fit(X[batch])

        assert_array_equal(scaler_batch.max_abs_, scaler_incr.max_abs_)
        assert scaler_batch.n_samples_seen_ == scaler_incr.n_samples_seen_

        assert_array_equal(scaler_batch.scale_, scaler_incr.scale_)
        assert_array_equal(scaler_batch.transform(X), scaler_incr.transform(X))

        # Test std after 1 step
        batch0 = slice(0, chunk_size)
        scaler_batch = MaxAbsScaler().fit(X[batch0])
        scaler_incr = MaxAbsScaler().partial_fit(X[batch0])

        assert_array_equal(scaler_batch.max_abs_, scaler_incr.max_abs_)
        assert scaler_batch.n_samples_seen_ == scaler_incr.n_samples_seen_
        assert_array_equal(scaler_batch.scale_, scaler_incr.scale_)
        assert_array_equal(scaler_batch.transform(X), scaler_incr.transform(X))

        # Test std until the end of partial fits, and
        scaler_batch = MaxAbsScaler().fit(X)
        scaler_incr = MaxAbsScaler()    # Clean estimator
        for i, batch in enumerate(gen_batches(n, chunk_size)):
            scaler_incr = scaler_incr.partial_fit(X[batch])
            assert_correct_incr(i, batch_start=batch.start,
                                batch_stop=batch.stop, n=n,
                                chunk_size=chunk_size,
                                n_samples_seen=scaler_incr.n_samples_seen_)


def test_normalizer_l1():
    rng = cp.random.RandomState(0)
    X_dense = rng.randn(4, 5)

    # set the row number 3 to zero
    X_dense[3, :] = 0.0

    # check inputs that support the no-copy optim
    normalizer = Normalizer(norm='l1', copy=True)
    X_norm = normalizer.transform(X_dense)
    assert X_norm is not X_dense
    X_norm1 = X_norm

    normalizer = Normalizer(norm='l1', copy=False)
    X_norm = normalizer.transform(X_dense)
    assert X_norm is X_dense
    X_norm2 = X_norm

    for X_norm in (X_norm1, X_norm2):
        row_sums = cp.abs(X_norm).sum(axis=1)
        for i in range(3):
            assert_array_equal(row_sums[i], 1.0)
        assert_array_equal(row_sums[3], 0.0)


def test_normalizer_l2():
    rng = cp.random.RandomState(0)
    X_dense = rng.randn(4, 5)

    # set the row number 3 to zero
    X_dense[3, :] = 0.0

    normalizer = Normalizer(norm='l2', copy=True)
    X_norm1 = normalizer.transform(X_dense)
    assert X_norm1 is not X_dense
    X_norm1 = cp.asnumpy(X_norm1)

    normalizer = Normalizer(norm='l2', copy=False)
    X_norm2 = normalizer.transform(X_dense)
    assert X_norm2 is X_dense
    X_norm2 = cp.asnumpy(X_norm2)

    for X_norm in (X_norm1, X_norm2):
        for i in range(3):
            assert_almost_equal(la.norm(X_norm[i]), 1.0)
        assert_almost_equal(la.norm(X_norm[3]), 0.0)


def test_normalizer_max():
    rng = cp.random.RandomState(0)
    X_dense = rng.randn(4, 5)

    # set the row number 3 to zero
    X_dense[3, :] = 0.0

    normalizer = Normalizer(norm='max', copy=True)
    X_norm1 = normalizer.transform(X_dense)
    assert X_norm1 is not X_dense
    X_norm1 = X_norm1

    normalizer = Normalizer(norm='max', copy=False)
    X_norm2 = normalizer.transform(X_dense)
    assert X_norm2 is X_dense
    X_norm2 = X_norm2

    for X_norm in (X_norm1, X_norm2):
        row_maxs = X_norm.max(axis=1)
        for i in range(3):
            assert_array_equal(row_maxs[i], 1.0)
        assert_array_equal(row_maxs[3], 0.0)


def test_normalize():
    # Test normalize function
    # Only tests functionality not used by the tests for Normalizer.
    X = cp.random.RandomState(37).randn(3, 2)
    assert_array_equal(normalize(X, copy=False),
                       normalize(X.T, axis=0, copy=False).T)
    assert_raises(ValueError, normalize, cp.array([[0]]), axis=2)
    assert_raises(ValueError, normalize, cp.array([[0]]), norm='l3')

    rs = cp.random.RandomState(0)
    X_dense = rs.randn(10, 5)
    ones = cp.ones((10))
    for dtype in (cp.float32, cp.float64):
        for norm in ('l1', 'l2'):
            X_dense = X_dense.astype(dtype)
            X_norm = normalize(X_dense, norm=norm)
            assert X_norm.dtype == dtype

            X_norm = X_norm
            if norm == 'l1':
                row_sums = cp.abs(X_norm).sum(axis=1)
            else:
                X_norm_squared = X_norm**2
                row_sums = X_norm_squared.sum(axis=1)

            assert_array_equal(row_sums, ones)

    # Test return_norm
    X_dense = cp.array([[3.0, 0, 4.0], [1.0, 0.0, 0.0], [2.0, 3.0, 0.0]])
    for norm in ('l1', 'l2', 'max'):
        _, norms = normalize(X_dense, norm=norm, return_norm=True)
        if norm == 'l1':
            assert_array_equal(norms, cp.array([7.0, 1.0, 5.0]))
        elif norm == 'l2':
            assert_array_equal(norms, cp.array([5.0, 1.0, 3.60555127]))
        else:
            assert_array_equal(norms, cp.array([4.0, 1.0, 3.0]))


def test_binarizer():
    X_ = cp.array([[1, 0, 5], [2, 3, -1]])

    X = cp.array(X_.copy())

    binarizer = Binarizer(threshold=2.0, copy=True)
    X_bin = binarizer.transform(X)
    assert cp.sum(X_bin == 0) == 4
    assert cp.sum(X_bin == 1) == 2
    X_bin = binarizer.transform(X)

    binarizer = Binarizer(copy=True).fit(X)
    X_bin = binarizer.transform(X)
    assert X_bin is not X
    assert cp.sum(X_bin == 0) == 2
    assert cp.sum(X_bin == 1) == 4

    binarizer = Binarizer(copy=True)
    X_bin = binarizer.transform(X)
    assert X_bin is not X
    assert cp.sum(X_bin == 0) == 2
    assert cp.sum(X_bin == 1) == 4

    binarizer = Binarizer(copy=False)
    X_bin = binarizer.transform(X)
    assert X_bin is X

    binarizer = Binarizer(copy=False)
    X_float = cp.array([[1, 0, 5], [2, 3, -1]], dtype=cp.float64)
    X_bin = binarizer.transform(X_float)
    assert X_bin is X_float

    assert cp.sum(X_bin == 0) == 2
    assert cp.sum(X_bin == 1) == 4

    binarizer = Binarizer(threshold=-0.5, copy=True)
    X = cp.array(X_.copy())

    X_bin = binarizer.transform(X)
    assert cp.sum(X_bin == 0) == 1
    assert cp.sum(X_bin == 1) == 5
    X_bin = binarizer.transform(X)


def test_center_kernel():
    # Test that KernelCenterer is equivalent to StandardScaler
    # in feature space
    rng = cp.random.RandomState(0)
    X_fit = rng.random_sample((5, 4))
    scaler = StandardScaler(with_std=False)
    scaler.fit(X_fit)
    X_fit_centered = scaler.transform(X_fit)
    K_fit = cp.dot(X_fit, X_fit.T)

    # center fit time matrix
    centerer = KernelCenterer()
    K_fit_centered = cp.dot(X_fit_centered, X_fit_centered.T)

    K_fit_centered2 = centerer.fit_transform(K_fit)
    assert_array_equal(K_fit_centered, K_fit_centered2)

    # center predict time matrix
    X_pred = rng.random_sample((2, 4))
    K_pred = cp.dot(X_pred, X_fit.T)
    X_pred_centered = scaler.transform(X_pred)
    K_pred_centered = cp.dot(X_pred_centered, X_fit_centered.T)
    K_pred_centered2 = centerer.transform(K_pred)
    assert_array_equal(K_pred_centered, K_pred_centered2)


def test_fit_transform():
    rng = cp.random.RandomState(0)
    X = rng.random_sample((5, 4))
    for obj in ((StandardScaler(), Normalizer(), Binarizer())):
        X_transformed = obj.fit(X).transform(X)
        X_transformed2 = obj.fit_transform(X)
        assert_array_equal(X_transformed, X_transformed2)


def test_add_dummy_feature():
    X = cp.array([[1, 0], [0, 1], [0, 1]])
    X = add_dummy_feature(X)
    assert_array_equal(X, cp.array([[1, 1, 0], [1, 0, 1], [1, 0, 1]]))


def test_fit_cold_start():
    X = cp.array(iris.data)
    X_2d = X[:, :2]

    # Scalers that have a partial_fit method
    scalers = [StandardScaler(with_mean=False, with_std=False),
               MinMaxScaler(),
               MaxAbsScaler()]

    for scaler in scalers:
        scaler.fit_transform(X)
        # with a different shape, this may break the scaler unless the internal
        # state is reset
        scaler.fit_transform(X_2d)


@pytest.mark.parametrize("with_centering", [True, False])
@pytest.mark.parametrize("with_scaling", [True, False])
@pytest.mark.parametrize("X", [cp.random.randn(10, 3)])
def test_robust_scaler_attributes(X, with_centering, with_scaling):
    scaler = RobustScaler(with_centering=with_centering,
                          with_scaling=with_scaling)
    scaler.fit(X)

    if with_centering:
        assert isinstance(scaler.center_, cp.ndarray)
    else:
        assert scaler.center_ is None
    if with_scaling:
        assert isinstance(scaler.scale_, cp.ndarray)
    else:
        assert scaler.scale_ is None


def test_robust_scaler_2d_arrays():
    # Test robust scaling of 2d array along first axis
    rng = cp.random.RandomState(0)
    X = rng.randn(4, 5)
    X[:, 0] = 0.0  # first feature is always of zero

    scaler = RobustScaler()
    X_scaled = scaler.fit(X).transform(X)

    assert_array_almost_equal(np.median(cp.asnumpy(X_scaled), axis=0),
                              5 * [0.0])
    assert_array_equal(X_scaled.std(axis=0)[0], 0)


def test_robust_scaler_iris():
    X = cp.array(iris.data)
    scaler = RobustScaler()
    X_trans = scaler.fit_transform(X)
    assert_array_almost_equal(np.median(cp.asnumpy(X_trans), axis=0), 0)
    X_trans_inv = scaler.inverse_transform(X_trans)
    assert_array_equal(X, X_trans_inv)
    q = cp.percentile(X_trans, q=(25, 75), axis=0)
    iqr = q[1] - q[0]
    assert_array_equal(iqr, 1)


def test_robust_scaler_iris_quantiles():
    X = cp.array(iris.data)
    scaler = RobustScaler(quantile_range=(10, 90))
    X_trans = scaler.fit_transform(X)
    assert_array_almost_equal(np.median(cp.asnumpy(X_trans), axis=0), 0)
    X_trans_inv = scaler.inverse_transform(X_trans)
    assert_array_equal(X, X_trans_inv)
    q = cp.percentile(X_trans, q=(10, 90), axis=0)
    q_range = q[1] - q[0]
    assert_array_equal(q_range, 1)


def test_robust_scaler_invalid_range():
    for range_ in [
        (-1, 90),
        (-2, -3),
        (10, 101),
        (100.5, 101),
        (90, 50),
    ]:
        scaler = RobustScaler(quantile_range=range_)

        assert_raises_regex(ValueError, r'Invalid quantile range: \(',
                            scaler.fit, cp.array(iris.data))


def test_robust_scale_axis1():
    X = cp.array(iris.data)
    X_trans = robust_scale(X, axis=1)
    assert_array_almost_equal(np.median(cp.asnumpy(X_trans), axis=1), 0)
    q = cp.percentile(X_trans, q=(25, 75), axis=1)
    iqr = q[1] - q[0]
    assert_array_equal(iqr, 1)


def test_robust_scale_1d_array():
    X = cp.array(iris.data[:, 1])
    X_trans = robust_scale(X)
    assert_array_almost_equal(np.median(cp.asnumpy(X_trans)), 0)
    q = cp.percentile(X_trans, q=(25, 75))
    iqr = q[1] - q[0]
    assert_array_equal(iqr, 1)


def test_robust_scaler_zero_variance_features():
    # Check RobustScaler on toy data with zero variance features
    X = cp.array([[0., 1., +0.5],
                  [0., 1., -0.1],
                  [0., 1., +1.1]])

    scaler = RobustScaler()
    X_trans = scaler.fit_transform(X)

    # NOTE: for such a small sample size, what we expect in the third column
    # depends HEAVILY on the method used to calculate quantiles. The values
    # here were calculated to fit the quantiles produces by np.percentile
    # using numpy 1.9 Calculating quantiles with
    # scipy.stats.mstats.scoreatquantile or scipy.stats.mstats.mquantiles
    # would yield very different results!
    X_expected = cp.array([[0., 0., +0.0],
                           [0., 0., -1.0],
                           [0., 0., +1.0]])
    assert_array_equal(X_trans, X_expected)
    X_trans_inv = scaler.inverse_transform(X_trans)
    assert_array_equal(X, X_trans_inv)

    # make sure new data gets transformed correctly
    X_new = cp.array([[+0., 2., 0.5],
                      [-1., 1., 0.0],
                      [+0., 1., 1.5]])
    X_trans_new = cp.asnumpy(scaler.transform(X_new))
    X_expected_new = np.array([[+0., 1., +0.],
                               [-1., 0., -0.83333],
                               [+0., 0., +1.66667]])
    assert_array_almost_equal(X_trans_new, X_expected_new, decimal=3)