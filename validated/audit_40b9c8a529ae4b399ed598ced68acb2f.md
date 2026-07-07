### Title
`setWithdrawPool` Update Without Migration Strands LP Liquidity in Old Pool - (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.sol` exposes a `setWithdrawPool` setter that allows the owner to replace the active withdrawal pool address. When called, LP funds already deposited in the old `WithdrawPool` for fast withdrawals are stranded from the protocol's normal flow. The old pool's `submitWithdrawal` can no longer be triggered by the Clearinghouse, and no migration mechanism exists to move stranded liquidity to the new pool — a direct structural analog to the StWSX setter vulnerability class.

---

### Finding Description

`Clearinghouse.sol` contains a post-initialization setter for the `withdrawPool` address:

```solidity
function setWithdrawPool(address _withdrawPool) external onlyOwner {
    require(_withdrawPool != address(0));
    withdrawPool = _withdrawPool;
}
``` [1](#0-0) 

The `withdrawPool` is the contract that holds LP-provided liquidity for fast withdrawals and receives sequencer-submitted normal withdrawals. In `BaseWithdrawPool`, the `submitWithdrawal` function enforces that only the Clearinghouse can trigger it:

```solidity
function submitWithdrawal(...) public {
    require(msg.sender == clearinghouse);
    ...
}
``` [2](#0-1) 

Critically, the `clearinghouse` address stored inside `BaseWithdrawPool` is set once at initialization and has **no setter**:

```solidity
function _initialize(address _clearinghouse, address _verifier) internal initializer {
    clearinghouse = _clearinghouse;
    verifier = _verifier;
}
``` [3](#0-2) 

When `setWithdrawPool` is called to point to a new pool:

1. The Clearinghouse routes all future `submitWithdrawal` calls to the **new** pool.
2. The **old** pool's `submitWithdrawal` can no longer be triggered by the Clearinghouse (the Clearinghouse no longer references it).
3. LP liquidity deposited in the old pool for fast withdrawals is stranded — it cannot be used for new fast withdrawals routed through the Clearinghouse.
4. The `minIdx` state in the old pool is not synchronized with the new pool, creating a desynchronization in withdrawal index tracking.
5. There is no atomic migration path for LP funds from the old pool to the new pool.

The only recovery path is `removeLiquidity`, which is `onlyOwner` and requires a manual, non-atomic owner action:

```solidity
function removeLiquidity(uint32 productId, uint128 amount, address sendTo) external onlyOwner {
    handleWithdrawTransfer(getToken(productId), sendTo, amount);
}
``` [4](#0-3) 

---

### Impact Explanation

LP funds deposited into the old `WithdrawPool` for fast withdrawal liquidity provision are stranded from the protocol's normal flow the moment `setWithdrawPool` is called. Fast withdrawal users who expected liquidity from the old pool cannot have their requests processed through the new pool. The `minIdx` desynchronization means withdrawal index state is not carried over, potentially allowing replay of already-processed withdrawal indices against the new pool or blocking valid ones. While `removeLiquidity` allows the owner to manually recover funds, there is no atomic migration, creating a window of fund inaccessibility and protocol desynchronization.

---

### Likelihood Explanation

Low-to-medium. The function exists, is callable by the owner, and has no migration guard or deprecation check. Any legitimate upgrade of the withdrawal pool infrastructure (e.g., deploying a new `WithdrawPool` with a bug fix) would trigger this issue. The `ContractOwner.sol` pattern shows the protocol does perform post-deployment configuration changes. [5](#0-4) 

---

### Recommendation

Remove the `setWithdrawPool` setter so that `withdrawPool` can only be set during `initialize`, matching the resolution applied to the StWSX analog. If upgradeability is required, implement an atomic migration that:
1. Calls `removeLiquidity` on the old pool for all supported products.
2. Transfers recovered funds to the new pool.
3. Synchronizes `minIdx` from the old pool to the new pool before switching the `withdrawPool` pointer.

---

### Proof of Concept

1. LPs deposit liquidity into the current `WithdrawPool` (old pool) to service fast withdrawals.
2. Owner calls `Clearinghouse.setWithdrawPool(newPool)`.
3. `Clearinghouse.withdrawPool` now points to `newPool`.
4. Sequencer submits a normal withdrawal — `Clearinghouse` calls `newPool.submitWithdrawal(...)`. Old pool is bypassed.
5. A fast withdrawal user calls `oldPool.submitFastWithdrawal(...)` — the old pool still has the LP liquidity, but the Clearinghouse no longer routes sequencer withdrawals through it, so `minIdx` in the old pool is frozen.
6. LP funds in the old pool are inaccessible through the normal protocol flow. The new pool has no liquidity. Fast withdrawals fail or are unavailable until the owner manually calls `oldPool.removeLiquidity(...)` and re-deposits into the new pool — a non-atomic, multi-step process with no protocol-level guarantee. [1](#0-0) [6](#0-5)

### Citations

**File:** core/contracts/Clearinghouse.sol (L750-753)
```text
    function setWithdrawPool(address _withdrawPool) external onlyOwner {
        require(_withdrawPool != address(0));
        withdrawPool = _withdrawPool;
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L23-30)
```text
    function _initialize(address _clearinghouse, address _verifier)
        internal
        initializer
    {
        __Ownable_init();
        clearinghouse = _clearinghouse;
        verifier = _verifier;
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L81-114)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;

        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);

        (
            uint32 productId,
            address sendTo,
            uint128 transferAmount
        ) = resolveFastWithdrawal(transaction);
        IERC20Base token = getToken(productId);

        require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L116-132)
```text
    function submitWithdrawal(
        IERC20Base token,
        address sendTo,
        uint128 amount,
        uint64 idx
    ) public {
        require(msg.sender == clearinghouse);

        if (markedIdxs[idx]) {
            return;
        }
        markedIdxs[idx] = true;
        // set minIdx to most recent withdrawal submitted by sequencer
        minIdx = idx;

        handleWithdrawTransfer(token, sendTo, amount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L151-157)
```text
    function removeLiquidity(
        uint32 productId,
        uint128 amount,
        address sendTo
    ) external onlyOwner {
        handleWithdrawTransfer(getToken(productId), sendTo, amount);
    }
```

**File:** core/contracts/ContractOwner.sol (L48-68)
```text
    function initialize(
        address multisig,
        address _deployer,
        address _spotEngine,
        address _perpEngine,
        address _endpoint,
        address _clearinghouse,
        address _verifier,
        address payable _wrappedNative
    ) external initializer {
        require(_deployer == msg.sender, "expected deployed to initialize");
        __Ownable_init();
        transferOwnership(multisig);
        deployer = _deployer;
        spotEngine = SpotEngine(_spotEngine);
        perpEngine = PerpEngine(_perpEngine);
        endpoint = Endpoint(_endpoint);
        clearinghouse = IClearinghouse(_clearinghouse);
        verifier = Verifier(_verifier);
        wrappedNative = _wrappedNative;
    }
```
