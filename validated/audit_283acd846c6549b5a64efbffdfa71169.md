### Title
Unprotected `initialize` in `WithdrawPool` Enables Front-Running to Seize Ownership and Drain Pool Funds — (`File: core/contracts/WithdrawPool.sol`)

---

### Summary

`WithdrawPool.initialize()` is an `external` function with no caller restriction. When the proxy is deployed in one transaction and initialized in a separate transaction, an attacker monitoring the mempool can front-run the initialization, seize ownership, and set `clearinghouse` to an attacker-controlled address. This allows the attacker to call `submitWithdrawal` — which is gated only by `msg.sender == clearinghouse` — to drain all ERC-20 tokens held in the pool.

---

### Finding Description

`WithdrawPool` inherits from `BaseWithdrawPool` and exposes a public `initialize` entry point:

```solidity
// core/contracts/WithdrawPool.sol
function initialize(address _clearinghouse, address _verifier) external {
    _initialize(_clearinghouse, _verifier);
}
``` [1](#0-0) 

The internal `_initialize` is guarded only by OpenZeppelin's `initializer` modifier, which prevents *re-initialization* but does **not** restrict *who* calls it first:

```solidity
// core/contracts/BaseWithdrawPool.sol
function _initialize(address _clearinghouse, address _verifier)
    internal
    initializer
{
    __Ownable_init();
    clearinghouse = _clearinghouse;
    verifier = _verifier;
}
``` [2](#0-1) 

The two critical state variables written during initialization are:

- `clearinghouse` — the only address authorized to call `submitWithdrawal`:

```solidity
function submitWithdrawal(...) public {
    require(msg.sender == clearinghouse);
    ...
    handleWithdrawTransfer(token, sendTo, amount);
}
``` [3](#0-2) 

- `owner` (set via `__Ownable_init`) — the only address authorized to call `removeLiquidity`:

```solidity
function removeLiquidity(uint32 productId, uint128 amount, address sendTo)
    external onlyOwner {
    handleWithdrawTransfer(getToken(productId), sendTo, amount);
}
``` [4](#0-3) 

The deployment pattern for upgradeable proxies in Nado separates proxy deployment from initialization into two distinct transactions. The window between these two transactions is the attack surface.

---

### Impact Explanation

An attacker who front-runs `initialize` gains:

1. **Full ownership** of the `WithdrawPool` contract, enabling `removeLiquidity` calls to transfer any token balance to an arbitrary address.
2. **Control of the `clearinghouse` slot**, enabling `submitWithdrawal` calls (which only check `msg.sender == clearinghouse`) to transfer arbitrary token amounts to any `sendTo` address.

Both paths result in complete drainage of all ERC-20 collateral tokens held in the `WithdrawPool`. The `WithdrawPool` is the settlement layer for user withdrawals and fast-withdrawal liquidity, so the asset impact is direct and total.

---

### Likelihood Explanation

The attack requires only:
- Monitoring the public mempool for the `WithdrawPool` proxy deployment transaction.
- Submitting `initialize(attackerAddress, attackerVerifier)` with a higher gas price before the deployer's initialization transaction lands.

No privileged access, leaked keys, or social engineering is required. The attack is executable by any unprivileged external actor on any chain with a public mempool (including Ink Chain, the target deployment chain for Nado). The attacker needs no prior relationship with the protocol.

---

### Recommendation

Add a deployer-only guard to `WithdrawPool.initialize` so that only the expected deployer address can trigger initialization:

```solidity
function initialize(address _clearinghouse, address _verifier) external {
    require(msg.sender == EXPECTED_DEPLOYER, "unauthorized");
    _initialize(_clearinghouse, _verifier);
}
```

Alternatively, deploy the proxy and call `initialize` atomically in a single transaction using a factory or the proxy constructor's `_data` parameter (as OpenZeppelin's `TransparentUpgradeableProxy` supports). This eliminates the initialization window entirely, which is the same remediation pattern recommended in the external report.

---

### Proof of Concept

```
1. Deployer broadcasts: deploy WithdrawPool proxy (tx A, pending in mempool)
2. Attacker sees tx A in mempool
3. Attacker broadcasts with higher gas:
       WithdrawPool(proxy).initialize(
           address(attackerClearinghouse),  // attacker-controlled contract
           address(attackerVerifier)
       )
4. Attacker's tx lands first; attacker is now owner, clearinghouse = attackerClearinghouse
5. Deployer's initialization tx reverts (initializer modifier: already initialized)
6. Attacker calls:
       WithdrawPool(proxy).submitWithdrawal(
           IERC20Base(usdcToken),
           attackerEOA,
           poolBalance,   // full balance
           anyIdx
       )
   → passes require(msg.sender == clearinghouse) since msg.sender == attackerClearinghouse
   → handleWithdrawTransfer sends all USDC to attackerEOA
``` [3](#0-2) [1](#0-0)

### Citations

**File:** core/contracts/WithdrawPool.sol (L16-18)
```text
    function initialize(address _clearinghouse, address _verifier) external {
        _initialize(_clearinghouse, _verifier);
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
