### Title
Unprotected `initialize` in `WithdrawPool` Allows Front-Running Attacker to Seize Ownership and Drain Liquidity Pool Funds — (File: `core/contracts/WithdrawPool.sol`)

---

### Summary
`WithdrawPool.initialize` is an `external` function with no caller restriction. Any unprivileged attacker who observes the proxy deployment transaction in the mempool can front-run the initialization, become the contract owner, and immediately drain all liquidity held in the pool via `removeLiquidity`.

---

### Finding Description
`WithdrawPool` is an upgradeable proxy contract that holds real ERC-20 collateral used to service both slow and fast withdrawals. Its `initialize` entry point is declared `external` with no access-control modifier:

```solidity
// core/contracts/WithdrawPool.sol
contract WithdrawPool is BaseWithdrawPool {
    function initialize(address _clearinghouse, address _verifier) external {
        _initialize(_clearinghouse, _verifier);
    }
}
```

The `initializer` guard lives on the internal `_initialize` in `BaseWithdrawPool`:

```solidity
// core/contracts/BaseWithdrawPool.sol
function _initialize(address _clearinghouse, address _verifier)
    internal
    initializer
{
    __Ownable_init();          // sets msg.sender as owner
    clearinghouse = _clearinghouse;
    verifier = _verifier;
}
```

`__Ownable_init()` unconditionally assigns `msg.sender` as the contract owner. Because `initialize` carries no `onlyOwner`, `require(msg.sender == deployer)`, or equivalent guard, the first external caller — not the deploying team — becomes the permanent owner and controls both `clearinghouse` and `verifier`.

`BaseWithdrawPool` does call `_disableInitializers()` in its constructor, which protects the bare implementation address. However, the proxy instance's storage is independent; the `initializer` flag in proxy storage is unset until `initialize` is called on the proxy, leaving the window open.

---

### Impact Explanation
An attacker who wins the front-run gains three compounding capabilities:

1. **Direct fund theft via `removeLiquidity`** — `onlyOwner` is the sole guard:
   ```solidity
   function removeLiquidity(uint32 productId, uint128 amount, address sendTo)
       external onlyOwner {
       handleWithdrawTransfer(getToken(productId), sendTo, amount);
   }
   ```
   The attacker can immediately sweep every ERC-20 token balance held by the pool.

2. **Blocking all legitimate slow-mode withdrawals** — `submitWithdrawal` enforces `require(msg.sender == clearinghouse)`. With `clearinghouse` set to the attacker's address, the real `Clearinghouse` can never call this function, permanently freezing user withdrawals.

3. **Approving fraudulent fast withdrawals** — `submitFastWithdrawal` delegates signature verification to `verifier`. With a malicious verifier, the attacker can approve arbitrary fast-withdrawal payouts without valid sequencer signatures.

Corrupted state: `clearinghouse` storage slot, `verifier` storage slot, `_owner` storage slot, and all ERC-20 balances of the pool.

---

### Likelihood Explanation
The attack requires only:
- Monitoring the public mempool for the `WithdrawPool` proxy deployment transaction.
- Submitting `initialize(attackerAddress, attackerAddress)` with a higher gas price before the protocol's own initialization transaction is mined.

No privileged access, leaked keys, or social engineering is required. Front-running proxy initialization is a well-known, mechanically straightforward attack on public EVM networks. The `WithdrawPool` is a standalone contract that may be deployed and initialized in separate transactions, widening the window.

---

### Recommendation
Add an explicit caller restriction to `WithdrawPool.initialize` so only the deployer or a trusted address can invoke it:

```solidity
address private immutable _deployer;

constructor() {
    _deployer = msg.sender;
    _disableInitializers();
}

function initialize(address _clearinghouse, address _verifier) external {
    require(msg.sender == _deployer, "only deployer");
    _initialize(_clearinghouse, _verifier);
}
```

Alternatively, deploy and initialize the proxy atomically in a single transaction (e.g., using a factory or deployment script that calls `initialize` in the same transaction as proxy creation), eliminating the front-running window entirely.

---

### Proof of Concept

1. Protocol broadcasts a transaction to deploy the `WithdrawPool` proxy (pointing to the `WithdrawPool` implementation).
2. Attacker observes the pending deployment in the mempool.
3. Attacker submits, with higher gas:
   ```solidity
   WithdrawPool(proxyAddress).initialize(
       address(attacker),   // malicious clearinghouse
       address(attacker)    // malicious verifier
   );
   ```
4. Attacker's transaction is mined first. `__Ownable_init()` sets `attacker` as owner; `clearinghouse` and `verifier` are set to `attacker`.
5. Attacker calls:
   ```solidity
   WithdrawPool(proxyAddress).removeLiquidity(
       QUOTE_PRODUCT_ID,
       type(uint128).max,
       attacker
   );
   ```
6. All USDC (or other collateral) held in the pool is transferred to the attacker.
7. The protocol's own `initialize` call subsequently reverts because the `initializer` flag is already set, leaving the pool permanently under attacker control. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/WithdrawPool.sol (L16-18)
```text
    function initialize(address _clearinghouse, address _verifier) external {
        _initialize(_clearinghouse, _verifier);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L18-21)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
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
