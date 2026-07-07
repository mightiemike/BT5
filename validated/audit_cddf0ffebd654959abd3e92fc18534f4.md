### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables Token Drain Without Providing USDC — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking the return value. The contract already declares `using ERC20Helper for IERC20Base` which provides a safe wrapper, but the function bypasses it. If the configured USDC token on Ink chain returns `false` on a failed transfer instead of reverting, any unprivileged caller can drain usdcE from any victim's `DirectDepositV1` contract without providing any USDC.

---

### Finding Description

`ContractOwner.sol` declares `using ERC20Helper for IERC20Base` at line 24, which attaches the `safeTransferFrom` wrapper that checks the return value via low-level call. However, `replaceUsdcEWithUsdc` at line 616 calls the raw `IERC20Base.transferFrom` directly:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

The `ERC20Helper.safeTransferFrom` implementation (lines 23–42 of `ERC20Helper.sol`) explicitly checks:

```solidity
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
```

The bare call at line 616 performs no such check. If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink chain (chainid 57073) returns `false` on a failed transfer, execution continues silently. The function then proceeds to:

1. Call `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` — which transfers all usdcE from the victim's DDA to `ContractOwner`.
2. Call `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` — which sends that usdcE to the attacker.

The function has no `onlyOwner` or `onlyDeployer` modifier — it is callable by any external address. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

An attacker with zero USDC balance calls `replaceUsdcEWithUsdc(subaccount)` targeting any subaccount whose `directDepositV1` holds usdcE. If `transferFrom` silently returns `false`:

- The attacker contributes no USDC.
- All usdcE held in the victim's `DirectDepositV1` is transferred to `ContractOwner` and then forwarded to the attacker via `safeTransfer`.
- The victim's `DirectDepositV1` is drained of its usdcE balance, which represents real deposited collateral.

This is a direct asset theft: the attacker receives usdcE tokens belonging to other users without providing the required USDC in exchange. [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation

The function is restricted to `block.chainid == 57073` (Ink chain). The USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink chain is a non-standard deployment whose exact revert-on-failure behavior is not guaranteed by the ERC20 standard. Tokens that return `false` instead of reverting (e.g., ZRX, BAT, cUSDC) are a known class. The function is publicly callable with no access control, meaning any user on Ink chain can attempt this. Likelihood is medium-low but non-zero given the chain-specific token deployment. [6](#0-5) 

---

### Recommendation

Replace the bare `transferFrom` call with the `safeTransferFrom` method already available via `ERC20Helper`:

```solidity
// Before (vulnerable):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

This uses the existing `ERC20Helper.safeTransferFrom` which checks both the call success and the decoded boolean return value, consistent with how all other token transfers in the codebase are handled. [2](#0-1) 

---

### Proof of Concept

1. Identify a subaccount `S` whose `directDepositV1Address[S]` is non-zero and holds usdcE balance `B > 0`.
2. Attacker (with 0 USDC, no approval given) calls `ContractOwner.replaceUsdcEWithUsdc(S)` on Ink chain.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, B)` executes. If USDC returns `false` (no revert), execution continues.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` transfers `B` usdcE to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, B)` sends `B` usdcE to the attacker.
6. Attacker receives `B` usdcE; victim's DDA is drained; no USDC was transferred. [1](#0-0)

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
