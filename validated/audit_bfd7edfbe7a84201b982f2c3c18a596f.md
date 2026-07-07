### Title
Unrestricted `processSlowModeTransactionImpl` Allows Any Caller to Execute Privileged Slow-Mode Transactions — (File: `core/contracts/EndpointTx.sol`)

### Summary
`processSlowModeTransactionImpl` in `EndpointTx.sol` is declared `public` with no access control modifier. This function is the internal dispatcher for all slow-mode transaction types and is intended to be invoked only by the `Endpoint` contract's own slow-mode processing logic. Because it is `public`, any external caller can invoke it directly on the `Endpoint` contract (via the delegatecall fallback to `endpointTx`), bypassing the slow-mode queue entirely and executing privileged operations — including `WithdrawInsurance`, `DumpFees`, `DelistProduct`, and `LinkSigner` — with attacker-controlled parameters.

### Finding Description

`EndpointTx.sol` implements `processSlowModeTransactionImpl` as a `public` function with no access restriction: [1](#0-0) 

The function dispatches over all slow-mode `TransactionType` variants. Several of these branches contain no `validateSender` check and pass attacker-controlled bytes directly to downstream privileged contracts:

- **`WithdrawInsurance`** — passes raw `transaction` bytes directly to `clearinghouse.withdrawInsurance(transaction, nSubmissions)`: [2](#0-1) 

- **`DumpFees`** — calls `offchainExchange.dumpFees()` and then `clearinghouse.claimSequencerFees(fees)`, zeroing out all `sequencerFee` entries: [3](#0-2) 

- **`DelistProduct`** — passes raw bytes to `clearinghouse.delistProduct(transaction)`: [4](#0-3) 

- **`LinkSigner` (slow-mode path)** — sets `linkedSigners[txn.sender]` to an arbitrary address with only a `validateSender` check (no signature required): [5](#0-4) 

The `Endpoint` contract stores `endpointTx` and upgrades it via `upgradeEndpointTx`, indicating a delegatecall-based dispatch architecture: [6](#0-5) 

When `processSlowModeTransactionImpl` is called on the `Endpoint` contract (routed via the fallback delegatecall to `EndpointTx`), it executes in `Endpoint`'s storage context. Downstream calls to `clearinghouse.withdrawInsurance` originate from `Endpoint`'s address, satisfying the `onlyEndpoint` modifier on the clearinghouse: [7](#0-6) 

### Impact Explanation

An unprivileged attacker can call `Endpoint.processSlowModeTransactionImpl(attacker_address, maliciousTxBytes)` directly. For the `WithdrawInsurance` branch, the attacker supplies arbitrary `transaction` bytes encoding a `WithdrawInsurance` struct with a `sendTo` address of their choice. The call reaches `clearinghouse.withdrawInsurance` with `msg.sender == Endpoint`, passing the `onlyEndpoint` guard, and insurance funds are transferred to the attacker. For `DumpFees`, the attacker can trigger premature fee settlement at any time, corrupting sequencer fee accounting. For `LinkSigner`, the attacker bypasses the slow-mode queue and sets a linked signer for their own subaccount without sequencer processing or fee payment.

The corrupted state deltas are: `insurance` balance in `ClearinghouseStorage`, `sequencerFee[productId]` mappings in `EndpointStorage`, and `linkedSigners[subaccount]` in `EndpointStorage`. [8](#0-7) 

### Likelihood Explanation

The entry point is a `public` function on a deployed contract reachable by any EOA or contract. No privileged role, leaked key, or social engineering is required. The attacker only needs to ABI-encode a valid `TransactionType` byte prefix followed by the appropriate struct. This is trivially constructable from the public ABI. Likelihood is **high**.

### Recommendation

Add an access control modifier to `processSlowModeTransactionImpl` restricting it to internal callers only. The simplest fix is to change the visibility from `public` to `internal`, or add an `onlyEndpoint`-equivalent guard that checks `msg.sender` is the canonical `Endpoint` proxy address. This mirrors the fix recommended in the original report: add an `onlyDiamond()`-equivalent modifier so the function is only callable through the intended privileged entry point.

### Proof of Concept

1. Attacker encodes a `WithdrawInsurance` transaction:
   ```solidity
   bytes memory txData = abi.encodePacked(
       uint8(IEndpoint.TransactionType.WithdrawInsurance),
       abi.encode(IEndpoint.WithdrawInsurance({amount: insuranceBalance, sendTo: attacker}))
   );
   ```
2. Attacker calls directly:
   ```solidity
   IEndpointTx(endpointAddress).processSlowModeTransactionImpl(attacker, txData);
   ```
3. `Endpoint`'s fallback delegatecalls into `EndpointTx.processSlowModeTransactionImpl`.
4. The `WithdrawInsurance` branch executes `clearinghouse.withdrawInsurance(txData, nSubmissions)` with `msg.sender == Endpoint`, passing `onlyEndpoint`.
5. Insurance funds are transferred to `attacker`.

### Citations

**File:** core/contracts/EndpointTx.sol (L200-208)
```text
    // TODO: these do not need senders or nonces
    // we can save some gas by creating new structs
    function processSlowModeTransactionImpl(
        address sender,
        bytes calldata transaction
    ) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );
```

**File:** core/contracts/EndpointTx.sol (L232-239)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
```

**File:** core/contracts/EndpointTx.sol (L240-253)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawInsurance) {
            clearinghouse.withdrawInsurance(transaction, nSubmissions);
        } else if (txType == IEndpoint.TransactionType.DelistProduct) {
            clearinghouse.delistProduct(transaction);
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

**File:** core/contracts/Endpoint.sol (L368-375)
```text
    function upgradeEndpointTx(address _endpointTx) external {
        require(
            msg.sender ==
                IProxyManager(_getProxyManager()).getProxyManagerHelper(),
            ERR_UNAUTHORIZED
        );
        endpointTx = _endpointTx;
    }
```

**File:** core/contracts/EndpointGated.sol (L25-31)
```text
    modifier onlyEndpoint() {
        require(
            msg.sender == endpoint,
            "SequencerGated: caller is not the endpoint"
        );
        _;
    }
```

**File:** core/contracts/EndpointStorage.sol (L48-52)
```text
    mapping(uint32 => int128) internal sequencerFee;

    mapping(bytes32 => address) internal linkedSigners;

    mapping(bytes32 => address) internal nlpSigners;
```
