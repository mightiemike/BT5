### Title
ERC-20 Slow Mode Fees Transferred to Endpoint Contract With No Recovery Mechanism - (File: `core/contracts/EndpointStorage.sol`)

### Summary
The `chargeSlowModeFee` function in `EndpointStorage.sol` transfers ERC-20 tokens (slow mode fees) directly to `address(this)` — the `Endpoint` contract itself. The `Endpoint` contract has no ERC-20 withdrawal or rescue function. The `DumpFees` slow-mode transaction in `EndpointTx.sol` only clears the `sequencerFee` accounting mapping and credits `X_ACCOUNT` inside the engines; it never transfers the actual ERC-20 tokens sitting in the `Endpoint` out. Every slow-mode fee paid by any unprivileged user is therefore permanently locked in the `Endpoint` contract.

### Finding Description

`EndpointStorage.chargeSlowModeFee` is called for every slow-mode transaction submitted by a non-owner, non-`DepositInsurance` caller:

```solidity
// EndpointStorage.sol
function chargeSlowModeFee(IERC20Base token, address from) internal virtual {
    require(address(token) != address(0));
    token.safeTransferFrom(from, address(this), clearinghouse.getSlowModeFee());
}
``` [1](#0-0) 

The call site in `EndpointTx.submitSlowModeTransactionImpl` confirms this path is taken for all non-privileged transaction types:

```solidity
} else {
    chargeSlowModeFee(_getQuote(), sender);
    slowModeFees += SLOW_MODE_FEE;
}
``` [2](#0-1) 

The `DumpFees` slow-mode transaction — the only fee-collection mechanism in the protocol — only resets the `sequencerFee` accounting mapping and calls `clearinghouse.claimSequencerFees`. It never touches the ERC-20 balance held by the `Endpoint` contract itself:

```solidity
} else if (txType == IEndpoint.TransactionType.DumpFees) {
    IOffchainExchange(offchainExchange).dumpFees();
    ...
    clearinghouse.claimSequencerFees(fees);   // engine accounting only
``` [3](#0-2) 

`rebalanceXWithdraw` withdraws from the `Clearinghouse` token balance, not from the `Endpoint`:

```solidity
function rebalanceXWithdraw(...) external onlyEndpoint {
    ...
    withdrawCollateral(X_ACCOUNT, txn.productId, txn.amount, txn.sendTo, nSubmissions);
}
``` [4](#0-3) 

The `Endpoint` contract (`Endpoint.sol`) exposes no `withdraw`, `rescue`, or ERC-20 sweep function. Tokens transferred to it via `chargeSlowModeFee` have no exit path. [5](#0-4) 

### Impact Explanation
Every slow-mode fee paid by any user (e.g., for `WithdrawCollateral`, `LinkSigner`, `ClaimBuilderFee`) is an ERC-20 transfer into the `Endpoint` contract that can never be recovered. Over the protocol's lifetime these fees accumulate and are permanently lost to the protocol treasury. The broken invariant is: *all protocol fee revenue must be claimable by the protocol*. The `slowModeFees` counter grows but the corresponding token balance in `Endpoint` is irrecoverable.

### Likelihood Explanation
Every unprivileged user who submits a slow-mode transaction triggers `chargeSlowModeFee`. This is a normal, documented user flow (e.g., slow-mode withdrawal, signer linking). No special conditions are required. Likelihood is high.

### Recommendation
In `DumpFees` processing (or a dedicated owner-callable function), transfer the accumulated ERC-20 slow-mode fee balance from the `Endpoint` to the `Clearinghouse` (or directly credit `X_ACCOUNT`) so it can be claimed via the normal `rebalanceXWithdraw` path. Alternatively, route `chargeSlowModeFee` to `address(clearinghouse)` directly instead of `address(this)`.

### Proof of Concept
1. Alice calls `Endpoint.submitSlowModeTransaction` with a `WithdrawCollateral` payload.
2. `EndpointTx.submitSlowModeTransactionImpl` reaches the `else` branch and calls `chargeSlowModeFee(_getQuote(), Alice)`.
3. `chargeSlowModeFee` executes `token.safeTransferFrom(Alice, address(this), fee)` — tokens land in the `Endpoint` contract.
4. `slowModeFees += SLOW_MODE_FEE` is incremented.
5. The sequencer later submits a `DumpFees` slow-mode transaction. `claimSequencerFees` credits `X_ACCOUNT` in the engine for `sequencerFee` entries — the ERC-20 balance in `Endpoint` is untouched.
6. No function in `Endpoint` can move those tokens out. They are permanently locked.

### Citations

**File:** core/contracts/EndpointStorage.sol (L83-93)
```text
    function chargeSlowModeFee(IERC20Base token, address from)
        internal
        virtual
    {
        require(address(token) != address(0));
        token.safeTransferFrom(
            from,
            address(this),
            clearinghouse.getSlowModeFee()
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L244-253)
```text
        } else if (txType == IEndpoint.TransactionType.DumpFees) {
            IOffchainExchange(offchainExchange).dumpFees();
            uint32[] memory spotIds = spotEngine.getProductIds();
            int128[] memory fees = new int128[](spotIds.length);
            for (uint256 i = 0; i < spotIds.length; i++) {
                fees[i] = sequencerFee[spotIds[i]];
                sequencerFee[spotIds[i]] = 0;
            }
            requireSubaccount(X_ACCOUNT);
            clearinghouse.claimSequencerFees(fees);
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/Clearinghouse.sol (L327-343)
```text
    function rebalanceXWithdraw(bytes calldata transaction, uint64 nSubmissions)
        external
        onlyEndpoint
    {
        IEndpoint.RebalanceXWithdraw memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.RebalanceXWithdraw)
        );

        withdrawCollateral(
            X_ACCOUNT,
            txn.productId,
            txn.amount,
            txn.sendTo,
            nSubmissions
        );
    }
```

**File:** core/contracts/Endpoint.sol (L23-66)
```text
contract Endpoint is
    EIP712Upgradeable,
    OwnableUpgradeable,
    EndpointStorage,
    IEndpoint
{
    using ERC20Helper for IERC20Base;

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
