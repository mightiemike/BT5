### Title
Unchecked `transferFrom()` Return Value Allows Draining usdcE from DirectDepositV1 Accounts Without Payment — (`File: core/contracts/ContractOwner.sol`)

### Summary
`ContractOwner.replaceUsdcEWithUsdc()` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking the boolean return value. If the USDC token at the hardcoded address on chain 57073 (Ink) returns `false` instead of reverting on a failed transfer, any unprivileged caller can drain usdcE from any `DirectDepositV1` account without paying any USDC.

### Finding Description
In `ContractOwner.replaceUsdcEWithUsdc()`, the function is designed to atomically swap usdcE held in a `DirectDepositV1` (DDA) contract for USDC provided by the caller. The intended flow is:

1. Read the usdcE balance of the DDA.
2. Pull USDC from `msg.sender` into the DDA.
3. Withdraw usdcE from the DDA to `ContractOwner`.
4. Forward usdcE to `msg.sender`.

At step 2, the raw `transferFrom()` call is made directly on the `IERC20Base` interface without checking its return value:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
``` [1](#0-0) 

Steps 3 and 4 proceed unconditionally regardless of whether step 2 succeeded. By contrast, the outbound transfer at step 4 uses `safeTransfer`, which does check the return value:

```solidity
IERC20Base(usdcE).safeTransfer(msg.sender, balance);
``` [2](#0-1) 

The `ERC20Helper.safeTransferFrom` wrapper that correctly checks return values exists in the codebase and is used elsewhere, but is not used here: [3](#0-2) 

The function has no access control modifier — it is `external` with only a chain ID guard (`block.chainid == 57073`), making it reachable by any unprivileged caller on the Ink chain. [4](#0-3) 

### Impact Explanation
If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on chain 57073 returns `false` on a failed transfer (e.g., insufficient allowance or balance) rather than reverting, an attacker can:

1. Call `replaceUsdcEWithUsdc(subaccount)` for any subaccount whose DDA holds usdcE.
2. Provide zero USDC allowance — the `transferFrom` silently returns `false`.
3. Receive the full usdcE balance of the DDA at no cost.

The corrupted asset delta is: attacker gains `balance` usdcE; DDA loses `balance` usdcE; ContractOwner receives and forwards usdcE without receiving any USDC in return. This is a direct theft of protocol-held collateral tokens.

### Likelihood Explanation
The function is callable by any address on chain 57073 with no permission check beyond the chain ID. The only prerequisite is that a DDA for some subaccount holds a non-zero usdcE balance. The exploitability depends on whether the specific USDC deployment on Ink returns `false` or reverts on failure. Bridged or non-canonical USDC deployments on newer chains are more likely to exhibit non-reverting behavior than mainnet USDC.

### Recommendation
Replace the bare `transferFrom` call with the project's own `ERC20Helper.safeTransferFrom` (or the equivalent internal `safeTransferFrom` helper already present in `EndpointStorage`):

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [5](#0-4) 

### Proof of Concept
1. Identify any subaccount whose DDA (`directDepositV1Address[subaccount]`) holds a non-zero usdcE balance.
2. Call `ContractOwner.replaceUsdcEWithUsdc(subaccount)` from an EOA with zero USDC allowance granted to `ContractOwner`.
3. The `transferFrom` at line 616 returns `false` (no revert).
4. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` transfers usdcE to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` transfers usdcE to the attacker.
6. Attacker receives `balance` usdcE without paying any USDC. [6](#0-5)

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
