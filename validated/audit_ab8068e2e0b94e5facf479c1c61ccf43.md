### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables USDC.e Drain from DirectDepositV1 Contracts — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` uses a raw, unchecked `IERC20Base.transferFrom` call to pull USDC from `msg.sender` into a `DirectDepositV1` (DDA) contract. If this call returns `false` instead of reverting, the function continues executing: it withdraws USDC.e from the DDA and sends it to `msg.sender`. Because the function has no access control, any unprivileged caller on Ink mainnet (chain ID 57073) can trigger this path, draining USDC.e from any DDA that holds a balance.

---

### Finding Description

`ContractOwner.replaceUsdcEWithUsdc` is a token-swap helper intended to replace USDC.e held in a DDA with USDC. The function is callable by any address on Ink mainnet (chain ID 57073) — there is no `onlyOwner` or similar modifier. [1](#0-0) 

The critical line is:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
``` [2](#0-1) 

The return value of this `transferFrom` call is **not checked**. Every other token transfer in the codebase uses `ERC20Helper.safeTransferFrom`, which wraps the call and requires `success && (data.length == 0 || abi.decode(data, (bool)))`: [3](#0-2) 

If `transferFrom` returns `false` (i.e., the caller has not approved USDC or has insufficient balance, and the token does not revert), execution continues:

1. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` — pulls all USDC.e from the DDA into `ContractOwner`.
2. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` — sends that USDC.e to the attacker. [4](#0-3) 

The DDA's USDC.e balance — deposited by a user awaiting `creditDeposit()` — is drained without any USDC being provided in exchange.

---

### Impact Explanation

A user who has sent USDC.e to their DDA (waiting for `creditDepositV1` to be called) loses those tokens. The attacker receives USDC.e at zero cost. The subaccount's internal balance in `SpotEngine` is unaffected (it has not yet been credited), so the protocol's on-chain accounting does not reflect the loss — the tokens simply disappear from the DDA before they can be credited.

The exact corrupted asset delta: USDC.e balance of the DDA (`IERC20Base(usdcE).balanceOf(directDepositV1)`) goes to zero; the attacker gains that amount; the depositing user's funds are permanently lost.

---

### Likelihood Explanation

The function is permissionless on Ink mainnet (chain ID 57073). The only precondition is that a DDA exists with a non-zero USDC.e balance. Likelihood depends on whether the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink reverts on failure or returns `false`. Modern USDC implementations typically revert, which would prevent silent failure. However, the absence of a return-value check is a concrete integration defect that violates the protocol's own standard (all other transfers use `safeTransferFrom`), and the exploitability is non-zero if the token's behavior differs from expectation or changes via upgrade.

---

### Recommendation

Replace the raw `transferFrom` call with `ERC20Helper.safeTransferFrom` (or the equivalent `safeTransferFrom` wrapper already used throughout the codebase):

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [3](#0-2) 

Additionally, consider adding an `onlyOwner` modifier to `replaceUsdcEWithUsdc`, since it is a privileged migration helper and should not be callable by arbitrary addresses.

---

### Proof of Concept

1. User deposits USDC.e into their DDA (e.g., by sending tokens directly to the DDA address). `creditDeposit()` has not yet been called.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` on Ink mainnet without approving any USDC.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, balance)` returns `false` (no revert).
4. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` transfers all USDC.e from the DDA to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, balance)` sends the USDC.e to the attacker.
6. The user's USDC.e is gone; their subaccount was never credited; the attacker profited. [1](#0-0)

### Citations

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```
