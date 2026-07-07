### Title
Missing Zero Address Validation in `BaseWithdrawPool._initialize()` Permanently Bricks Withdrawal Functionality — (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool._initialize()` stores `clearinghouse` and `verifier` without zero address checks. Both are persistent state variables with no post-initialization setter. If either is set to `address(0)` at deployment, the entire `WithdrawPool` becomes permanently non-functional, locking all user funds held in the contract.

---

### Finding Description

`BaseWithdrawPool._initialize()` assigns both critical addresses unconditionally:

```solidity
function _initialize(address _clearinghouse, address _verifier)
    internal
    initializer
{
    __Ownable_init();
    clearinghouse = _clearinghouse;   // no zero check
    verifier = _verifier;             // no zero check
}
``` [1](#0-0) 

These two variables are the load-bearing pillars of every withdrawal path in the contract:

**Path 1 — `submitWithdrawal` (sequencer-initiated):**

```solidity
function submitWithdrawal(...) public {
    require(msg.sender == clearinghouse);
    ...
    handleWithdrawTransfer(token, sendTo, amount);
}
``` [2](#0-1) 

If `clearinghouse == address(0)`, the `require` can never be satisfied by any real caller, permanently blocking all sequencer-routed withdrawals.

**Path 2 — `submitFastWithdrawal` (user-initiated):**

```solidity
Verifier v = Verifier(verifier);
v.requireValidTxSignatures(transaction, idx, signatures);
...
IERC20Base token = getToken(productId);
``` [3](#0-2) 

`getToken` calls `spotEngine()`, which calls `IClearinghouse(clearinghouse).getEngineByType(...)`:

```solidity
function spotEngine() internal view returns (ISpotEngine) {
    return ISpotEngine(
        IClearinghouse(clearinghouse).getEngineByType(
            IProductEngine.EngineType.SPOT
        )
    );
}
``` [4](#0-3) 

If `clearinghouse == address(0)`, this high-level call reverts (Solidity 0.8.x reverts on calls to addresses with no code), bricking `submitFastWithdrawal` as well. If `verifier == address(0)`, the `Verifier(verifier).requireValidTxSignatures(...)` call reverts for the same reason, bricking fast withdrawals independently.

Neither `clearinghouse` nor `verifier` has a post-initialization setter anywhere in `BaseWithdrawPool` or `WithdrawPool`. The `initializer` modifier ensures `_initialize` can only be called once. There is no recovery path short of a full proxy upgrade. [5](#0-4) 

---

### Impact Explanation

If `_clearinghouse` is set to `address(0)`:
- `submitWithdrawal` is permanently blocked (impossible `require(msg.sender == address(0))`).
- `submitFastWithdrawal` is permanently blocked (reverts inside `spotEngine()` → `getToken()`).
- All ERC-20 collateral held by the `WithdrawPool` is permanently locked; no withdrawal path remains.

If `_verifier` is set to `address(0)`:
- `submitFastWithdrawal` is permanently blocked (reverts on `Verifier(address(0)).requireValidTxSignatures(...)`).
- Fast-withdrawal liquidity providers cannot recover their funds via the fast path.

The asset delta is concrete: tokens already transferred into `WithdrawPool` become irrecoverable.

---

### Likelihood Explanation

Identical in class to M-02: an accidental zero passed to an initializer for a persistent, non-resettable state variable. The `WithdrawPool` is deployed and initialized once via the `ProxyManager` upgrade flow. A single mistyped argument during deployment produces a permanently broken contract. The M-02 report explicitly treats this class of deployer misconfiguration as Medium Risk.

---

### Recommendation

Add explicit zero address guards at the top of `_initialize`:

```solidity
function _initialize(address _clearinghouse, address _verifier)
    internal
    initializer
{
    require(_clearinghouse != address(0), "clearinghouse is zero address");
    require(_verifier != address(0), "verifier is zero address");
    __Ownable_init();
    clearinghouse = _clearinghouse;
    verifier = _verifier;
}
``` [1](#0-0) 

Apply the same pattern to `Clearinghouse.initialize()` for `_quote`, `_clearinghouseLiq`, and `_withdrawPool`, and to `Endpoint.initialize()` for `_sequencer`, `_offchainExchange`, `_clearinghouse`, `_verifier`, and `_endpointTx`. [6](#0-5) [7](#0-6) 

---

### Proof of Concept

1. Deploy `WithdrawPool` proxy and call `initialize(address(0), validVerifier)`.
2. The `initializer` modifier marks the contract as initialized; no second call is possible.
3. Call `submitFastWithdrawal(idx, transaction, signatures)` as any unprivileged user.
4. Execution reaches `getToken(productId)` → `spotEngine()` → `IClearinghouse(address(0)).getEngineByType(...)`.
5. Solidity 0.8.x reverts: "call to non-contract". Transaction fails.
6. Repeat for `submitWithdrawal`: `require(msg.sender == address(0))` fails unconditionally.
7. All funds held in `WithdrawPool` are permanently inaccessible. [8](#0-7) [2](#0-1)

### Citations

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

**File:** core/contracts/BaseWithdrawPool.sol (L81-113)
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

**File:** core/contracts/BaseWithdrawPool.sol (L206-213)
```text
    function spotEngine() internal view returns (ISpotEngine) {
        return
            ISpotEngine(
                IClearinghouse(clearinghouse).getEngineByType(
                    IProductEngine.EngineType.SPOT
                )
            );
    }
```

**File:** core/contracts/WithdrawPool.sol (L15-19)
```text
contract WithdrawPool is BaseWithdrawPool {
    function initialize(address _clearinghouse, address _verifier) external {
        _initialize(_clearinghouse, _verifier);
    }
}
```

**File:** core/contracts/Clearinghouse.sol (L25-40)
```text
    function initialize(
        address _endpoint,
        address _quote,
        address _clearinghouseLiq,
        uint256 _spreads,
        address _withdrawPool
    ) external initializer {
        __Ownable_init();
        setEndpoint(_endpoint);
        quote = _quote;
        clearinghouse = address(this);
        clearinghouseLiq = _clearinghouseLiq;
        spreads = _spreads;
        withdrawPool = _withdrawPool;
        emit ClearinghouseInitialized(_endpoint, _quote);
    }
```

**File:** core/contracts/Endpoint.sol (L31-66)
```text
    function initialize(
        address _sanctions,
        address _sequencer,
        address _offchainExchange,
        IClearinghouse _clearinghouse,
        address _verifier,
        address _endpointTx
    ) external initializer {
        __Ownable_init();
        __EIP712_init("Nado", "0.0.1");
        sequencer = _sequencer;
        clearinghouse = _clearinghouse;
        offchainExchange = _offchainExchange;
        verifier = IVerifier(_verifier);
        sanctions = ISanctionsList(_sanctions);
        endpointTx = _endpointTx;
        spotEngine = ISpotEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.SPOT)
        );
        perpEngine = IPerpEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.PERP)
        );
        slowModeConfig = SlowModeConfig({timeout: 0, txCount: 0, txUpTo: 0});
        priceX18[QUOTE_PRODUCT_ID] = ONE;

        if (nlpPools.length == 0) {
            nlpPools.push(
                NlpPool({
                    poolId: 0,
                    subaccount: N_ACCOUNT,
                    owner: address(0),
                    balanceWeightX18: uint128(ONE)
                })
            );
        }
    }
```
